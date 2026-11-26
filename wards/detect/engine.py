"""Choose the detectors that apply to each segment and merge what they find."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterable

from wards.detect.address import AddressDetector
from wards.detect.demographics import MAX_AGE, detect_ages, detect_dates
from wards.detect.ids import detect_ids
from wards.detect.names import NameDetector
from wards.detect.tokens import Tokens
from wards.lexicon import HONORIFICS_LATIN, Lexicon
from wards.model import CLIPPED, NAME_TYPES, Channel, Document, Mention, Role, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.veto import Veto

FIELD_PRIORITY = 100

_NULL_VALUE = re.compile(r"(?i)^(?:n/?a|nil|none|null|unknown|not[ \t]+(?:known|available)|-+|\.+|0)$")
_NAME_FIELDS = {F.PATIENT_NAME: Role.PATIENT, F.FIRST_NAME: Role.PATIENT,
                F.NAME: Role.PATIENT,
                F.LAST_NAME: Role.PATIENT, F.RELATIVE_NAME: Role.RELATIVE,
                F.PERSON_NAME: Role.UNKNOWN}
_ID_ONLY_FIELDS = frozenset({F.ORG, F.DISTRICT, F.STATE, F.SEX, F.DOCTOR_NAME, F.CLINICAL})
_RELATION_PREFIX = re.compile(
    r"(?i)^[ \t]*(?:[sdwch][ \t]*/[ \t]*o|son[ \t]+of|daughter[ \t]+of|wife[ \t]+of|care[ \t]+of)"
    r"(?![A-Za-z])\.?[ \t]*[:\-]?[ \t]*")
_DOCTOR_PREFIX = re.compile(r"^[ \t]*(?:(?:Dr|DR|Doctor)(?![A-Za-z])|डॉ|डा\.)")
_PHONE_CHUNK = re.compile(r"\+?[\dXx*•(][\dXx*• \t\-().]{4,}[\dXx*•]")
_MASK = re.compile(r"[Xx*•]{3,}")
_LEADING_AGE = re.compile(r"^[ \t]*(?P<age>\d{1,3})(?:\.\d+)?(?![\d./])")
_AGE_BAND = re.compile(r"^[ \t]*(?:\d{1,3}[ \t]*(?:-|–|to)[ \t]*\d{1,3}|\d{1,3}[ \t]*\+)")
_NEGATIVE_AGE = re.compile(r"^[ \t]*[\-−–][ \t]*\d")
_INFANT = re.compile(r"(?i)^[ \t]*\d{1,3}[ \t]*(?:months?|mon\b|mths?|weeks?|wks?|days?|माह|महीन|दिन|हफ)")
_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
_HEX_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{2}){12,}(?![A-Za-z0-9])")
_PATH_GAP = re.compile(r"^[_ \-.]$")
_EMBEDDED_ID = re.compile(r"\d{4}")
_PERSON_INDEX = re.compile(r"(?i)(?:name|patient|naam|नाम|मरीज)")


def resolve(mentions: Iterable[Mention], text: str) -> list[Mention]:
    """Non-overlapping mentions that cover everything any detector found.

    Overlapping names are merged. Otherwise the stronger mention wins and what the weaker
    one covered beyond it is kept as a clipped mention, so no fragment survives.
    """
    names = sorted((m for m in mentions if m.etype in NAME_TYPES), key=lambda m: (m.start, m.end))
    merged: list[Mention] = []
    for mention in names:
        if merged and mention.start < merged[-1].end:
            last = merged[-1]
            strong = max(last, mention, key=lambda m: (m.priority, m.end - m.start))
            start, end = last.start, max(last.end, mention.end)
            merged[-1] = Mention(strong.etype, start, end, text[start:end], role=strong.role,
                                 priority=strong.priority, source=strong.source, data=strong.data)
        else:
            merged.append(mention)
    others = [m for m in mentions if m.etype not in NAME_TYPES]
    ordered = sorted(merged + others,
                     key=lambda m: (-m.priority, -(m.end - m.start), m.start, m.etype.value))
    accepted: list[Mention] = []
    for mention in ordered:
        if mention.end <= mention.start:
            continue
        covering = [a for a in accepted if mention.overlaps(a.start, a.end)]
        if not covering:
            accepted.append(mention)
        elif mention.etype not in _WHOLE_ONLY:
            accepted.extend(_leftovers(mention, covering, text))
    return sorted(accepted, key=lambda m: m.start)


# A date or age means nothing in part: its replacement is computed from the whole value.
_WHOLE_ONLY = frozenset({E.DATE, E.DOB, E.AGE})
_MIN_LEFTOVER_DIGITS = 3


def _leftovers(mention: Mention, covering: list[Mention], text: str) -> list[Mention]:
    pieces, cursor = [], mention.start
    for other in sorted(covering, key=lambda m: m.start):
        if other.start > cursor:
            pieces.append((cursor, other.start))
        cursor = max(cursor, other.end)
    if cursor < mention.end:
        pieces.append((cursor, mention.end))
    found = []
    for start, end in pieces:
        while start < end and not _is_word_char(text[start]):
            start += 1
        while end > start and not _is_word_char(text[end - 1]):
            end -= 1
        value = text[start:end]
        # digits of an identifier, or a capitalised or Devanagari word of a name or place
        telling = sum(c.isdigit() for c in value) >= _MIN_LEFTOVER_DIGITS or (
            mention.etype in _WORDY and any(
                len(w) >= 3 and (w[0].isupper() or not w.isascii()) for w in value.split()))
        if telling:
            found.append(Mention(mention.etype, start, end, value, role=mention.role,
                                 priority=mention.priority, source=CLIPPED))
    return found


def _is_word_char(char: str) -> bool:
    return unicodedata.category(char)[0] in "LNM"


_WORDY = NAME_TYPES | {E.VILLAGE, E.LOCALITY, E.LANDMARK, E.TEHSIL}


class Detector:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.names = NameDetector(lexicon)
        self.address = AddressDetector(lexicon)
        self.veto = Veto(lexicon)

    def detect_document(self, doc: Document) -> None:
        for segment in doc.segments:
            segment.mentions = self.detect_segment(segment)

    def detect_segment(self, segment: Segment) -> list[Mention]:
        text = segment.view.text
        if not text.strip():
            return []
        field = segment.field
        if field is F.ATTACHMENT:
            return [Mention(E.OTHER_ID, 0, len(text), text, priority=FIELD_PRIORITY,
                            source="field", data="attachment")]
        if field is F.CODE:
            found = []
        elif segment.channel in (Channel.HEADER, Channel.KEY):
            found = detect_ids(text) + self.names.gazetteer(text)
            if _EMBEDDED_ID.search(text) or _PERSON_INDEX.search(segment.label):
                found += self.words_as_names(text)
        elif field is F.PATH:
            found = self.path(text)
        elif field is F.DISTRICT and not self.lexicon.district(text.strip()):
            found = self.free_text(text)          # shifted column: not a district at all
        elif field in _ID_ONLY_FIELDS:
            found = [] if segment.numeric else detect_ids(text)
        elif field is F.TEXT:
            if segment.numeric:
                found = [m for m in detect_ids(text) if m.start == 0 and m.end == len(text)]
            else:
                found = self.free_text(text)
        else:
            structured = self.structured(field, text)
            if structured is None:
                found = [] if segment.numeric else self.free_text(text)
            else:
                found = structured
                if not segment.numeric:
                    found = found + detect_ids(text)
        return resolve(self.veto.apply(text, found), text)

    # ----------------------------------------------------------------- free text

    def free_text(self, text: str, embedded: bool = True) -> list[Mention]:
        tokens = Tokens(text, self.lexicon)
        ids = detect_ids(text)
        found = ids + self.names.detect(text, tokens) + self.address.detect_free(text)
        if any(ch.isdigit() for ch in text):
            found += detect_ages(text) + detect_dates(text)
            phones = [m for m in ids if m.etype in (E.PHONE, E.PHONE_MASKED)]
            found += self.names.before_phone(text, phones, tokens)
        if embedded:
            found += self.embedded(text)
        return found

    def embedded(self, text: str) -> list[Mention]:
        """Base64 or hex payloads inside text: if they hide personal data, remove them."""
        found = []
        for pattern, decoder in ((_BASE64_RUN, _from_base64), (_HEX_RUN, _from_hex)):
            for m in pattern.finditer(text):
                payload = decoder(m.group(0))
                if payload is None:
                    continue
                inner = self.free_text(payload, embedded=False)
                if any(x.etype not in (E.DATE, E.AGE) for x in
                       self.veto.apply(payload, inner)):
                    found.append(Mention(E.OTHER_ID, m.start(), m.end(), m.group(0),
                                         priority=99, source="embedded"))
        return found

    def words_as_names(self, text: str) -> list[Mention]:
        """Every run of name-like words, for places where data sits where a label should."""
        tokens = Tokens(text, self.lexicon)
        found, index = [], 0
        while index < len(tokens):
            run = tokens.run_forward(index)
            if not run:
                index += 1
                continue
            index = run[-1] + 1
            # an all-capitals fragment of a few letters is an ID prefix, not a name
            if len(run) == 1:
                word = tokens[run[0]].text
                if word.isascii() and word.isupper() and len(word) <= 4:
                    continue
            start, end = tokens.span(run)
            if not self.lexicon.district(text[start:end]):
                found.append(Mention(E.PATIENT_NAME, start, end, text[start:end],
                                     priority=60, source="path"))
        return found

    # --------------------------------------------------------------------- paths

    def path(self, text: str) -> list[Mention]:
        """Names and numbers glued into a file name or export path."""
        tokens = Tokens(text, self.lexicon)
        found = detect_ids(text)
        run: list[int] = []

        def flush() -> None:
            if not run:
                return
            words = [tokens[i].text for i in run]
            known = [self.lexicon.is_known_name(w) for w in words]
            capital = all(w[0].isupper() or tokens[run[0]].deva for w in words)
            if capital:
                is_name = any(known) or len(run) >= 2
            else:
                is_name = sum(known) >= 2
            if is_name:
                start, end = tokens.span(run)
                found.append(Mention(E.PATIENT_NAME, start, end, text[start:end],
                                     priority=70, source="path"))
            run.clear()

        for index, token in enumerate(tokens.items):
            word = token.text
            usable = len(word) > 1 and not self.lexicon.is_stopword(word) \
                and not (word.isascii() and word.isupper() and len(word) <= 4
                         and not self.lexicon.is_known_name(word))
            if usable and not token.deva and word[0].islower():
                usable = self.lexicon.is_known_name(word)
            if run and (not usable
                        or not _PATH_GAP.match(text[tokens[run[-1]].end:token.start])):
                flush()
            if usable:
                run.append(index)
        flush()
        for m in re.finditer(r"(?<!\d)\d{6,}(?!\d)", text):
            found.append(Mention(E.OTHER_ID, m.start(), m.end(), m.group(0), priority=58,
                                 source="path"))
        return found

    # ---------------------------------------------------------------- structured

    def structured(self, field: F, text: str) -> list[Mention] | None:
        """Mentions for a typed value, or ``None`` when the value does not fit the type."""
        start = len(text) - len(text.lstrip())
        end = len(text.rstrip())
        value = text[start:end]
        if not value or _NULL_VALUE.match(value):
            return []
        digits = sum(ch.isdigit() for ch in value)

        def whole(etype: E, low: int = start, high: int = end, role: Role = Role.UNKNOWN,
                  data: object = None) -> list[Mention]:
            return [Mention(etype, low, high, text[low:high], role=role,
                            priority=FIELD_PRIORITY, source="field", data=data)]

        if field is F.NAME and not self._person_like(value):
            return None
        if field in _NAME_FIELDS:
            return self._name_field(text, start, end, _NAME_FIELDS[field])
        if field is F.PHONE:
            chunks = []
            for m in _PHONE_CHUNK.finditer(text):
                count = sum(c.isdigit() for c in m.group(0))
                masked = bool(_MASK.search(m.group(0)))
                if count >= 6 or masked and count >= 2:
                    etype = E.PHONE_MASKED if masked else E.PHONE
                    chunks += whole(etype, m.start(), m.end())
            return chunks or (whole(E.PHONE) if digits >= 5 else None)
        if field is F.AADHAAR:
            if _MASK.search(value):
                return whole(E.AADHAAR_MASKED)
            return whole(E.AADHAAR) if digits >= 8 else None
        if field is F.ABHA_NUMBER:
            if "@" in value:
                return whole(E.ABHA_ADDRESS)
            return whole(E.ABHA_NUMBER) if digits >= 6 else None
        if field is F.ABHA_ADDRESS:
            return whole(E.ABHA_ADDRESS) if len(value) >= 3 else []
        if field is F.PAN:
            return whole(E.PAN) if digits and len(value) >= 8 else None
        if field is F.EMAIL:
            return whole(E.EMAIL) if "@" in value else None
        if field is F.MRN:
            return whole(E.MRN) if digits >= 3 else None
        if field is F.INSURANCE_ID:
            return whole(E.INSURANCE_ID) if digits >= 4 else None
        if field is F.DOB:
            dates = detect_dates(text, typed=True, is_dob=True)
            if dates:
                for mention in dates:
                    mention.priority, mention.source = FIELD_PRIORITY, "field"
                return dates
            return whole(E.DOB) if digits else None
        if field is F.DATE:
            dates = detect_dates(text, typed=True)
            for mention in dates:
                mention.priority = FIELD_PRIORITY - 1
            return dates or None
        if field in (F.AGE, F.AGE_SEX):
            if _AGE_BAND.match(value):
                return []                       # already generalised
            if _NEGATIVE_AGE.match(value):
                return whole(E.AGE, data="invalid")  # impossible, and unique enough to identify
            m = _LEADING_AGE.match(text)
            if not m or int(m.group("age")) > MAX_AGE:
                return None
            if _INFANT.search(value):
                return whole(E.AGE, data="infant")
            integral = Mention(E.AGE, m.start("age"), m.end(), m.group("age"),
                               priority=FIELD_PRIORITY, source="field")
            return [integral]
        if field is F.ADDRESS:
            return self.address.parse(text) + self.names.detect(text)
        if field is F.VILLAGE:
            if self.lexicon.district(value):
                return []
            return whole(E.VILLAGE) if any(c.isalpha() for c in value) else None
        if field is F.PIN:
            compact = value.replace(" ", "")
            return whole(E.PIN) if compact.isdigit() and len(compact) == 6 else None
        simple = {F.HOUSE: E.HOUSE, F.LOCALITY: E.LOCALITY, F.LANDMARK: E.LANDMARK,
                  F.TEHSIL: E.TEHSIL}
        if field in simple:
            return whole(simple[field])
        return None

    def _person_like(self, value: str) -> bool:
        """Is the value of an unowned "name" key a person rather than a thing?"""
        tokens = Tokens(value, self.lexicon)
        if not len(tokens) or self.veto.name_spans(value, tokens):
            return False
        if tokens.known([i for i in range(len(tokens)) if tokens.is_candidate(i)] or [0]):
            return True
        return 2 <= len(tokens) <= 5 and all(tokens.is_candidate(i) for i in range(len(tokens)))

    def _name_field(self, text: str, start: int, end: int,
                    role: Role) -> list[Mention] | None:
        value = text[start:end]
        if any(ch.isdigit() for ch in value):
            return None
        if _DOCTOR_PREFIX.match(value):
            return []
        relation = _RELATION_PREFIX.match(value)
        if relation:
            start += relation.end()
            role = Role.RELATIVE
        tokens = Tokens(text, self.lexicon)
        words = [t for t in tokens.items if t.start >= start and t.end <= end]
        if len(words) > 8:
            return None

        def filler(index: int) -> bool:
            word = words[index].text
            # in a name field a word that is also a medicine or condition is a name
            if self.lexicon.is_known_name(word) or self.lexicon.is_medical(word):
                return False
            return word.lower() in HONORIFICS_LATIN or self.lexicon.is_stopword(word)

        low, high = 0, len(words)
        while low < high and filler(low):
            low += 1
        while high > low and filler(high - 1):
            high -= 1
        if low >= high:
            return []
        first, last = words[low].start, words[high - 1].end
        etype = E.RELATIVE_NAME if role is Role.RELATIVE else E.PATIENT_NAME
        return [Mention(etype, first, last, text[first:last], role=role,
                        priority=FIELD_PRIORITY, source="field")]


def _from_base64(token: str) -> str | None:
    if len(token.rstrip("=")) % 4 == 1:
        return None
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
        return _printable(raw.decode("utf-8"))
    except (binascii.Error, ValueError):
        return None


def _from_hex(token: str) -> str | None:
    try:
        return _printable(bytes.fromhex(token).decode("utf-8"))
    except ValueError:
        return None


def _printable(text: str) -> str | None:
    if text and all(c.isprintable() or c in "\r\n\t" for c in text):
        return text
    return None

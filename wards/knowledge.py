"""What one file reveals helps clean the others.

A hospital number labelled in a discharge summary also appears unlabelled in a nursing note
header, a file name or a metadata path. After the first detection pass this module learns
identifier values and shapes, places, and village-to-district pairs, then sweeps every
segment for them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from wards.detect.address import LAST_PLACE, AddressDetector
from wards.lexicon import District, Lexicon
from wards.model import CLIPPED, Channel, Document, Mention, Record, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import digits_only, id_key, is_devanagari, phone_key, skeleton, transliterate

_SWEEP_PRIORITY = 91
_ID_PARTS = re.compile(r"[A-Za-z]+|\d+|[^A-Za-z\d]+")
_DIGIT_GROUPS = re.compile(r"(?<!\d)\+?\d(?:[ \t\-./()]{0,2}\d){7,19}(?!\d)")
_PAN_LIKE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])")
_MAILBOX = re.compile(r"[\w.+\-]+@[\w.\-]+\w")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{5,}(?!\d)")
_DIGIT_TYPES = (E.AADHAAR, E.ABHA_NUMBER, E.PHONE)
_SHAPED_TYPES = (E.MRN, E.INSURANCE_ID)
# A known number is found whatever separates its parts ("TQV/R/3318" in "tqv_r_3318.txt").
_CHUNK = re.compile(r"[A-Za-z0-9]+")
_NUMBER_GAP = re.compile(r"^[ \t]?[/\-_.\\]?[ \t]?$")
_MAX_CHUNKS = 7
_SHORT_CORE = re.compile(r"(?<!\d)\d{4}(?!\d)")
_YEAR = re.compile(r"(?:19|20)\d\d$")


def place_key(text: str) -> str:
    """Cross-script key for a village or tehsil: रामपुर matches Rampur."""
    words = re.findall(r"[A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+", text)
    parts = (skeleton(transliterate(w) if is_devanagari(w) else w)[0] for w in words)
    return "".join(parts)


def id_shape(value: str, min_digits: int = 5) -> str | None:
    """A regex for identifiers that look like ``value``: letters literal, digits counted."""
    pieces, letters, digits = [], 0, 0
    parts = _ID_PARTS.findall(value.strip())
    for part in parts:
        if part.isascii() and part.isalpha():
            pieces.append(re.escape(part.upper()))
            letters += len(part)
        elif part.isdigit():
            pieces.append(rf"\d{{{len(part)}}}")
            digits += len(part)
        elif re.fullmatch(r"[ \t/\-_.]{1,2}", part):
            pieces.append(r"[ /\-_.]?")
        else:
            return None
    # Without a letter prefix the shape would match any lab value of the same length.
    if not 2 <= letters <= 12 or digits < min_digits or not parts[0].isalpha():
        return None
    return "".join(pieces)


class Knowledge:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.address = AddressDetector(lexicon)
        self.shapes: dict[str, E] = {}
        self.id_values: dict[str, E] = {}
        self.digit_values: dict[str, E] = {}
        self.digit_cores: set[str] = set()
        self.short_cores: set[str] = set()              # four digits: file names only
        self.numbers: dict[str, E] = {}                 # one person's numbers, by id_key
        self.villages: dict[str, str] = {}              # casefolded surface -> surface
        self.village_districts: dict[str, Counter] = defaultdict(Counter)
        self._shape_regex: re.Pattern[str] | None = None
        self._village_regex: re.Pattern[str] | None = None

    # ------------------------------------------------------------------ learning

    def learn(self, docs: Iterable[Document]) -> None:
        # imported here: wards.anchors reads names as linking does, and linking uses this file
        from wards import anchors

        docs = list(docs)
        sightings = anchors.Sightings(docs, self.lexicon)
        anchors.demote_shared(sightings)
        for value, etype in anchors.declared_numbers(sightings):
            self._learn_number(value, etype)
        for value in anchors.field_numbers(sightings) + anchors.repeated_numbers(sightings):
            self._learn_number(value, E.MRN, anchored=True)
        for doc in docs:
            for segment in doc.segments:
                for mention in segment.mentions:
                    self._learn_mention(mention)
            for record in doc.records:
                self._learn_places(record)
        self._compile()

    def _learn_mention(self, mention: Mention) -> None:
        if mention.source == CLIPPED:
            return
        value = mention.value.strip()
        if mention.etype in _SHAPED_TYPES:
            key = id_key(value)
            if len(key) >= 5 and any(c.isdigit() for c in key):
                self.id_values.setdefault(key, mention.etype)
                shape = id_shape(value)
                if shape:
                    self.shapes.setdefault(shape, mention.etype)
                runs = re.findall(r"\d{5,}", value)
                if runs and mention.etype is E.MRN:
                    self.digit_cores.add(runs[-1])
                if key.isdigit() and len(key) >= 8:
                    self.digit_values.setdefault(key, mention.etype)
        elif mention.etype in _DIGIT_TYPES:
            digits = digits_only(value)
            if mention.etype is E.PHONE:
                digits = phone_key(value)
            if len(digits) >= 8:
                self.digit_values.setdefault(digits, mention.etype)
        elif mention.etype is E.PAN:
            self.id_values.setdefault(value.upper(), E.PAN)
        elif mention.etype in (E.EMAIL, E.ABHA_ADDRESS):
            self.id_values.setdefault(value.lower(), mention.etype)
        elif mention.etype in (E.VILLAGE, E.LOCALITY):
            self._learn_village(mention, value)

    def _learn_village(self, mention: Mention, value: str) -> None:
        """Villages to search for everywhere; from a combined cell, also its last private
        part."""
        places = []
        if mention.etype is E.VILLAGE \
                and not any(self.lexicon.is_stopword(w) for w in value.split()):
            places.append(value)
        if "," in value:
            places.append(self.address.village_in(value))
        elif mention.data == LAST_PLACE:
            places.append(value)
        for place in places:
            if place and len(place) >= 4 and not self.lexicon.district(place):
                self.villages.setdefault(place.casefold(), place)

    def _learn_number(self, value: str, etype: E, anchored: bool = False) -> None:
        """A number to look for in every file, key and file name."""
        key = id_key(value)
        letters = sum(c.isalpha() for c in key)
        # short or bare numbers could be anything elsewhere
        if len(key) >= (5 if anchored else 6) and (letters or len(key) >= (6 if anchored else 7)):
            self.numbers.setdefault(key, etype)
        if etype is not E.MRN:
            return
        runs = [r for r in re.findall(r"\d{4,}", value) if not _YEAR.match(r)]
        if runs:
            (self.digit_cores if len(runs[-1]) >= 5 else self.short_cores).add(runs[-1])
        # a prefix of three letters or more is telling enough to find its other numbers
        shape = id_shape(value, min_digits=4) if anchored and letters >= 3 else None
        if shape:
            self.shapes.setdefault(shape, etype)

    def _learn_places(self, record: Record) -> None:
        villages = [m.value for _, m in record.mentions() if m.etype is E.VILLAGE]
        if not villages:
            return
        district = self.record_district(record)
        if district:
            for village in villages:
                self.village_districts[place_key(village)][district.latin] += 1

    def record_district(self, record: Record) -> District | None:
        """The district named in this record, from a district field or an address."""
        for segment in record.segments:
            if segment.field is F.DISTRICT:
                found = self.lexicon.district(segment.view.text.strip())
                if found:
                    return found
        for segment in record.segments:
            text = segment.view.text
            for m in re.finditer(
                    r"(?i)(?:distt?\.?|district|जिला|ज़िला|ज़िला)[ \t]*[:\-]?[ \t]*"
                    r"([A-Za-z\u0900-\u097f]+(?:[ \t][A-Za-z\u0900-\u097f]+)?)", text):
                words = m.group(1).split()
                for count in range(len(words), 0, -1):
                    found = self.lexicon.district(" ".join(words[:count]))
                    if found:
                        return found
        return None

    def district_of(self, village: str) -> str | None:
        votes = self.village_districts.get(place_key(village))
        if not votes:
            return None
        return max(sorted(votes), key=lambda name: votes[name])

    def _compile(self) -> None:
        if self.shapes:
            body = "|".join(sorted(self.shapes, key=lambda s: (-len(s), s)))
            self._shape_regex = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])",
                                           re.IGNORECASE)
        if self.villages:
            body = "|".join(re.escape(v) for v in
                            sorted(self.villages.values(), key=lambda v: (-len(v), v)))
            self._village_regex = re.compile(
                rf"(?<![A-Za-z\u0900-\u097f])(?:{body})(?![A-Za-z\u0900-\u097f])", re.IGNORECASE)

    # ------------------------------------------------------------------ sweeping

    def sweep(self, segment: Segment, patient_context: bool) -> list[Mention]:
        """Mentions of learned values in one segment."""
        text = segment.view.text
        found: list[Mention] = []

        def add(etype: E, start: int, end: int, priority: int = _SWEEP_PRIORITY) -> None:
            found.append(Mention(etype, start, end, text[start:end], priority=priority,
                                 source="known"))

        if self._shape_regex is not None:
            for m in self._shape_regex.finditer(text):
                add(self._shape_type(m.group(0)), m.start(), m.end())
        for start, end, etype in self._known_numbers(text):
            # a number of unknown kind yields to any rule that knows what it is
            add(etype, start, end, _SWEEP_PRIORITY if etype is E.MRN else 58)
        if segment.field is F.PATH:
            for m in _SHORT_CORE.finditer(text):
                if m.group(0) in self.short_cores:
                    add(E.MRN, m.start(), m.end(), 59)
        for m in _DIGIT_GROUPS.finditer(text):
            digits = digits_only(m.group(0))
            etype = self.digit_values.get(digits)
            if etype is None and 10 <= len(digits) <= 13 \
                    and self.digit_values.get(digits[-10:]) is E.PHONE:
                etype = E.PHONE
            if etype is not None:
                add(etype, m.start(), m.end())
        for pattern, fold in ((_PAN_LIKE, str.upper), (_MAILBOX, str.lower)):
            for m in pattern.finditer(text):
                etype = self.id_values.get(fold(m.group(0)))
                if etype is not None:
                    add(etype, m.start(), m.end())
        loose = segment.channel is not Channel.CONTENT or segment.field is F.PATH \
            or segment.record is None
        if loose and not segment.numeric:
            for m in _LONG_DIGITS.finditer(text):
                if m.group(0) in self.digit_cores:
                    add(E.MRN, m.start(), m.end(), 59)
        if patient_context and self._village_regex is not None and not segment.numeric \
                and segment.field not in (F.CODE, F.ORG, F.DISTRICT, F.STATE, F.CLINICAL):
            for m in self._village_regex.finditer(text):
                add(E.VILLAGE, m.start(), m.end(), 54)
        return found

    def _known_numbers(self, text: str) -> Iterable[tuple[int, int, E]]:
        """The longest run of parts at each place that spells a known number."""
        if not self.numbers or not any(c.isdigit() for c in text):
            return
        chunks = list(_CHUNK.finditer(text))
        taken = 0
        for first, chunk in enumerate(chunks):
            if chunk.start() < taken:
                continue
            key, hit = "", None
            for last in range(first, min(first + _MAX_CHUNKS, len(chunks))):
                if last > first and not _NUMBER_GAP.match(
                        text[chunks[last - 1].end():chunks[last].start()]):
                    break
                key += chunks[last].group(0).upper()
                # bare digits split by marks are a date or a result, not the number
                if key in self.numbers and (last == first or not key.isdigit()):
                    hit = chunks[last].end(), self.numbers[key]
            if hit:
                taken = hit[0]
                yield chunk.start(), hit[0], hit[1]

    def _shape_type(self, value: str) -> E:
        known = self.id_values.get(id_key(value))
        if known is not None:
            return known
        for shape in sorted(self.shapes):
            if re.fullmatch(shape, value, re.IGNORECASE):
                return self.shapes[shape]
        return E.MRN

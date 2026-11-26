"""The final self-scan: look for anything we know to be personal in what we are releasing.

Detection decides what is personal; the auditor assumes detection or a writer may have
failed and searches the finished values for every strong identifier, full name and address
phrase seen anywhere in the input. It normalises text the way the leak scorer does
(compatibility forms, look-alike letters, zero-width characters, Indic digits, accents,
escapes and encoded payloads), so a disguised copy is still found.

A hit is repaired in place first. Only a file that still shows a hit after being written
and read back is quarantined.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from dataclasses import dataclass

from wards.lexicon import Lexicon
from wards.link import name_tokens
from wards.model import NAME_TYPES, Document, Mention, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import id_key, phone_key
from wards.textview import TextView
from wards.veto import Veto

NAME, IDENTIFIER, ADDRESS = "name", "identifier", "address"
REPLACEMENT = {NAME: "[NAME]", IDENTIFIER: "[ID]", ADDRESS: "[ADDRESS]"}

_ID_TYPES = frozenset({E.AADHAAR, E.ABHA_NUMBER, E.PHONE, E.MRN, E.INSURANCE_ID, E.PAN,
                       E.OTHER_ID})
_MAIL_TYPES = frozenset({E.EMAIL, E.ABHA_ADDRESS})
_PHRASE_TYPES = frozenset({E.VILLAGE, E.HOUSE, E.LANDMARK, E.LOCALITY})
# Only detections backed by a label, a field or another file are trusted enough to drive a
# search; a lone word-list guess could scrub medical text everywhere.
_MIN_NAME_PRIORITY = 51
_MIN_PHRASE_PRIORITY = 70
_MIN_ID_LENGTH = 7
_MAX_ID_CHUNKS = 7

_CHUNK = re.compile(r"[A-Za-z0-9]+")
_WORD = re.compile(r"[A-Za-z0-9]+|[ऀ-ॣॱ-ॿ]+")
_ID_GAP = re.compile(r"^[ \t\-./()_+]{0,3}$")
_NAME_GAP = re.compile(r"^(?:[ \t_\-]{1,2}|\. ?)$")
_PHRASE_GAP = re.compile(r"^[ \t.,\-/#:]{0,3}$")
_MAILBOX = re.compile(r"[\w.+\-]+@[\w.\-]+\w")
_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")
_HEX_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{2}){12,}(?![A-Za-z0-9])")
_WARD = re.compile(r"(?i)^(?:ward|वार्ड)\W*(?:no\W*)?\d+$")
_NO_NAME_FIELDS = frozenset({F.DOCTOR_NAME, F.ORG, F.DISTRICT, F.STATE, F.SEX})


@dataclass(frozen=True)
class Finding:
    start: int          # offsets in the scanned (source) string
    end: int
    kind: str


class PhraseSet:
    """Token tuples to search for, indexed by their first word."""

    def __init__(self) -> None:
        self.keys: set[tuple[str, ...]] = set()
        self.longest: dict[str, int] = {}

    def add(self, key: tuple[str, ...]) -> None:
        self.keys.add(key)
        self.longest[key[0]] = max(len(key), self.longest.get(key[0], 0))

    def __contains__(self, key: object) -> bool:
        return key in self.keys

    def __len__(self) -> int:
        return len(self.keys)


class Auditor:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.veto = Veto(lexicon)
        self.ids: set[str] = set()
        self.phones: set[str] = set()
        self.mailboxes: set[str] = set()
        self.names = PhraseSet()
        self.phrases = PhraseSet()
        self.digit_cores: set[str] = set()

    # ------------------------------------------------------------------ learning

    def learn(self, doc: Document) -> None:
        for segment in doc.segments:
            for mention in segment.mentions:
                self.remember(mention)

    def remember(self, mention: Mention) -> None:
        value, etype = mention.value.strip(), mention.etype
        if etype in NAME_TYPES:
            if mention.priority < _MIN_NAME_PRIORITY:
                return
            words = name_tokens(value, self.lexicon)
            distinctive = [w for w in words if not self.lexicon.is_generic(w)]
            if len(words) < 2 or not distinctive:
                return
            if mention.source == "path" \
                    and not any(self.lexicon.is_known_name(w) for w in words):
                return
            key = tuple(w.casefold() for w in words)
            self.names.add(key)
            self.names.add(key[-1:] + key[:-1])          # "Surname Given"
        elif etype in _ID_TYPES:
            if mention.source == "path" or mention.data == "attachment":
                return
            if etype is E.PHONE:
                digits = phone_key(value)
                if len(digits) == 10 and len(set(digits)) > 2:
                    self.phones.add(digits)
                    self.ids.add(digits)
                return
            key = id_key(value)
            if len(key) >= _MIN_ID_LENGTH and sum(c.isdigit() for c in key) >= 4 \
                    and len(set(key)) > 2:
                self.ids.add(key)
                runs = re.findall(r"\d{5,}", value)
                if etype is E.MRN and runs:
                    self.digit_cores.add(runs[-1])
        elif etype in _MAIL_TYPES:
            self.mailboxes.add(value.casefold())
        elif etype in _PHRASE_TYPES and mention.priority >= _MIN_PHRASE_PRIORITY:
            words = tuple(w.casefold() for w in _WORD.findall(value))
            joined = " ".join(words)
            if len(joined) >= 6 and not _WARD.match(value) and not self.lexicon.district(value):
                self.phrases.add(words)

    # ------------------------------------------------------------------ scanning

    def findings(self, text: str, names: bool = True, nested: bool = True) -> list[Finding]:
        """Everything forbidden in ``text``, as non-overlapping source spans."""
        if not text:
            return []
        view = TextView.of(text)
        normal = view.text
        spans: list[tuple[int, int, str]] = []
        spans += [(a, b, IDENTIFIER) for a, b in self._identifiers(normal)]
        words = list(_WORD.finditer(normal))
        if names:
            hits = self._windows(normal, words, self.names, _NAME_GAP)
            if hits:
                protected = self.veto.name_spans(normal)
                spans += [(a, b, NAME) for a, b in hits
                          if not any(a < end and start < b for start, end in protected)]
            spans += [(a, b, ADDRESS)
                      for a, b in self._windows(normal, words, self.phrases, _PHRASE_GAP)]
        found = []
        for start, end, kind in spans:
            low, high = view.source_span(start, end)
            found.append(Finding(low, high, kind))
        if nested:
            found += self._encoded(text, names)
        return _merge(found)

    def _identifiers(self, text: str) -> Iterable[tuple[int, int]]:
        if "@" not in text and not any(c.isdigit() for c in text):
            return
        chunks = list(_CHUNK.finditer(text))
        for first, chunk in enumerate(chunks):
            key = ""
            for last in range(first, min(first + _MAX_ID_CHUNKS, len(chunks))):
                if last > first and not _ID_GAP.match(
                        text[chunks[last - 1].end():chunks[last].start()]):
                    break
                key += chunks[last].group(0).upper()
                if len(key) > 24:
                    break
                phone = key.isdigit() and 11 <= len(key) <= 14 and key[-10:] in self.phones
                if key in self.ids or phone:
                    yield chunk.start(), chunks[last].end()
        for m in _MAILBOX.finditer(text):
            if m.group(0).casefold() in self.mailboxes:
                yield m.start(), m.end()

    @staticmethod
    def _windows(text: str, words: list[re.Match[str]], wanted: PhraseSet,
                 gap: re.Pattern[str]) -> list[tuple[int, int]]:
        if not wanted:
            return []
        found = []
        for first, word in enumerate(words):
            longest = wanted.longest.get(word.group(0).casefold())
            if longest is None:
                continue
            key: tuple[str, ...] = ()
            for last in range(first, min(first + longest, len(words))):
                if last > first and not gap.match(text[words[last - 1].end():words[last].start()]):
                    break
                key += (words[last].group(0).casefold(),)
                if key in wanted:
                    found.append((word.start(), words[last].end()))
        return found

    def _encoded(self, text: str, names: bool) -> list[Finding]:
        found = []
        for pattern, decoder in ((_BASE64_RUN, _from_base64), (_HEX_RUN, _from_hex)):
            for m in pattern.finditer(text):
                payload = decoder(m.group(0))
                if payload and self.findings(payload, names, nested=False):
                    found.append(Finding(m.start(), m.end(), IDENTIFIER))
        return found

    # ----------------------------------------------------------------- documents

    def segment_findings(self, segment: Segment) -> list[Finding]:
        if segment.drop or segment.field is F.ATTACHMENT:
            return []
        return self.findings(segment.final, names=segment.field not in _NO_NAME_FIELDS)

    def repair(self, doc: Document) -> int:
        """Overwrite every finding in the document. Returns the number of repairs."""
        repaired = 0
        for segment in doc.segments:
            findings = self.segment_findings(segment)
            if not findings:
                continue
            repaired += len(findings)
            if segment.numeric:
                segment.output = "0"
                continue
            value, out, cursor = segment.final, [], 0
            for finding in findings:
                original = value[finding.start:finding.end]
                token = "redacted" if segment.constrained else REPLACEMENT[finding.kind]
                out.append(value[cursor:finding.start])
                out.append(token + "".join(c for c in original if c in "\r\n"))
                cursor = finding.end
            out.append(value[cursor:])
            segment.output = "".join(out)
        return repaired

    def leaks(self, doc: Document) -> int:
        """Findings left in a document that was read back from the finished output."""
        return sum(len(self.segment_findings(segment)) for segment in doc.segments)

    def is_clean(self, text: str, path: bool = False) -> bool:
        """For file names, locators and other strings that cannot be repaired in place."""
        if self.findings(text):
            return False
        return not (path and any(core in self.digit_cores
                                 for core in re.findall(r"\d{5,}", text)))


def _merge(findings: list[Finding]) -> list[Finding]:
    merged: list[Finding] = []
    for item in sorted(findings, key=lambda f: (f.start, -f.end, f.kind)):
        if merged and item.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Finding(last.start, max(last.end, item.end), last.kind)
        else:
            merged.append(item)
    return merged


def _from_base64(token: str) -> str | None:
    if len(token.rstrip("=")) % 4 == 1:
        return None
    try:
        return base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None


def _from_hex(token: str) -> str | None:
    try:
        return bytes.fromhex(token).decode("utf-8")
    except ValueError:
        return None

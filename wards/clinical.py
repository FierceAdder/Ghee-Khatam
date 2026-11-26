"""Suppress a diagnosis that only one patient has (or very few).

"Someone with kala-azar was admitted that week" identifies a person however dates and ages
are treated. The decision is made per *diagnosis*, for the whole release: if a code is held
by no more than ``rare_diagnosis_patients`` patients, its code and every spelling of its
name are replaced in all patient records, so no record stands out by being treated
differently. Documents about no patient (protocols, formularies) keep it.

Which words name which code is learned from the data itself: ``code``/``display`` pairs in
JSON and FHIR, and ``Name (CODE)`` in text, in any language.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from wards.documents import JsonObject
from wards.model import DocKind, Document, Mention, Record, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F

CODE_REPLACEMENT = "R69"             # ICD-10: illness, unspecified
NAME_REPLACEMENT = "[CONDITION]"

_ICD = r"[A-TV-Z]\d{2}(?:\.\d{1,2})?"
_ICD_VALUE = re.compile(rf"^{_ICD}$")
_NAME_THEN_CODE = re.compile(
    rf"(?:^|[:;\n•]|\d\.)[ \t]*(?P<name>[^:;()\n\d\[\]]{{3,70}}?(?:[ \t]\([^()\n]{{2,40}}\))?)"
    rf"[ \t]*\((?P<code>{_ICD})\)")
_QUALIFIER = re.compile(r"(?i)^(?:unspecified|other|essential \(primary\))[ \t]+")
_PRIORITY = 97
_WORD_CHARS = r"A-Za-z0-9ऀ-ॿ"


def aliases(name: str) -> set[str]:
    """Shorter ways the same condition is written: "Dengue, unspecified" -> "dengue"."""
    base = " ".join(name.split()).casefold()
    forms = {base}
    bracket = re.search(r"\(([^()]{4,40})\)$", base)
    if bracket:
        forms.add(bracket.group(1))
        forms.add(base[:bracket.start()].strip())
    for form in list(forms):
        head = form.split(",")[0].strip()
        forms.add(head)
        forms.add(_QUALIFIER.sub("", head))
    # very short fragments would match ordinary words
    return {f for f in forms if len(f) >= 5}


class ConditionPlan:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self.names: dict[str, set[str]] = defaultdict(set)      # code -> spellings
        self.rare: frozenset[str] = frozenset()
        self.holders: dict[str, set[str]] = {}       # code -> pseudonyms (for counts only)
        self._codes: re.Pattern[str] | None = None
        self._words: re.Pattern[str] | None = None

    # ------------------------------------------------------------------ learning

    def learn(self, doc: Document) -> None:
        if doc.kind in (DocKind.JSON, DocKind.FHIR):
            self._walk(doc.payload)
        for segment in doc.segments:
            if not segment.numeric:
                for m in _NAME_THEN_CODE.finditer(segment.text):
                    self.names[m.group("code")] |= aliases(m.group("name"))

    def _walk(self, node: object, label: str = "") -> None:
        if isinstance(node, list):
            for child in node:
                self._walk(child, label)
        if not isinstance(node, JsonObject):
            return
        values = {key.text: value for key, value in node.entries}
        code = values.get("code")
        if isinstance(code, Segment) and _ICD_VALUE.match(code.text) \
                and self._is_diagnosis(label, values):
            for sibling in ("display", "text", "name", "description"):
                found = values.get(sibling)
                if isinstance(found, Segment):
                    self.names[code.text] |= aliases(found.text)
        for key, value in node.entries:
            self._walk(value, key.text)

    @staticmethod
    def _is_diagnosis(label: str, values: dict) -> bool:
        system = values.get("system")
        if isinstance(system, Segment):
            return "icd" in system.text.casefold()
        return bool(re.search(r"(?i)diagnos|condition|problem|disease|निदान", label))

    # ------------------------------------------------------------------ deciding

    def decide(self, records: Iterable[tuple[str, Record]]) -> None:
        """``records`` pairs every linked record with its patient's pseudonym."""
        if self.threshold <= 0 or not self.names:
            return
        everything = self._compile(self.names)
        holders: dict[str, set[str]] = defaultdict(set)
        by_name = {name: code for code in sorted(self.names) for name in self.names[code]}
        for patient, record in records:
            for segment in record.segments:
                text = segment.view.text
                for m in everything[0].finditer(text):
                    holders[m.group(0)].add(patient)
                for m in everything[1].finditer(text) if everything[1] else ():
                    holders[by_name[" ".join(m.group(0).split()).casefold()]].add(patient)
        self.holders = dict(holders)
        self.rare = frozenset(code for code, patients in holders.items()
                              if len(patients) <= self.threshold)
        if self.rare:
            self._codes, self._words = self._compile(
                {code: self.names[code] for code in self.rare})

    def _compile(self, names: dict[str, set[str]]):
        codes = "|".join(re.escape(c) for c in sorted(names, key=lambda c: (-len(c), c)))
        words = sorted({n for spellings in names.values() for n in spellings},
                       key=lambda n: (-len(n), n))
        code_pattern = re.compile(rf"(?<![A-Za-z0-9])(?:{codes})(?!\.?\d|[A-Za-z])")
        if not words:
            return code_pattern, None
        body = "|".join(r"[ \t]+".join(map(re.escape, w.split())) for w in words)
        return code_pattern, re.compile(rf"(?<![{_WORD_CHARS}])(?:{body})(?![{_WORD_CHARS}])",
                                        re.IGNORECASE)

    # ------------------------------------------------------------------ applying

    def mentions(self, segment: Segment) -> list[Mention]:
        if not self.rare or segment.numeric or segment.field is F.ATTACHMENT:
            return []
        text = segment.view.text
        found = []
        for pattern in (self._codes, self._words):
            if pattern is None:
                continue
            for m in pattern.finditer(text):
                found.append(Mention(E.DIAGNOSIS, m.start(), m.end(), m.group(0),
                                     priority=_PRIORITY, source="rare",
                                     data="code" if pattern is self._codes else "name"))
        return found

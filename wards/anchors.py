"""Hospital numbers recognised across a dataset, whatever their shape.

Shapes and labels cannot list every hospital's numbering. Two further kinds of evidence
need the whole dataset and the names already detected:

* a field (a column, key or form label) whose values look alike, are nearly unique per
  patient and sit beside patient names holds their numbers (:func:`field_numbers`);
* a code that appears with the same patient's name in two or more files is that
  patient's identifier (:func:`repeated_numbers`).

Linking joins records by these numbers, so a value seen with two different people (a ward,
package or protocol code) is never accepted.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from wards.columns import (EVENT_PREFIX, NumberCell, classify_label, identifier_skeleton,
                           normalize_label, personal_numbers)
from wards.detect.ids import NUMBER_LABEL
from wards.layout import pairs
from wards.lexicon import Lexicon
from wards.link import Name, jaro_winkler, name_groups, record_names
from wards.model import (CLIPPED, NAME_TYPES, Channel, DocKind, Document, Mention, Record, Role,
                         Segment)
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import id_key
from wards.numbers import foreign_number, label_words, reads_as_other, words_before
from wards.veto import Veto

# A code in running text: parts joined without spaces, holding a run of three digits.
_TOKEN = re.compile(r"(?<![A-Za-z0-9@])[A-Za-z0-9]+(?:[/\-_.\\][A-Za-z0-9]+)*(?![A-Za-z0-9@])")
_DIGIT_RUN = re.compile(r"\d{3}")
_OPEN_FIELDS = (F.TEXT, F.NAME, F.MRN)
_DECLARED = ("field", "label", "layout", "pattern")
_SAME_SPELLING = 0.92


@dataclass(frozen=True)
class Sighting:
    record: Record
    segment: Segment
    start: int
    value: str


def same_person(a: Name, b: Name) -> bool:
    """Stricter than linking: a shared first name is not enough."""
    return a.keys <= b.keys or b.keys <= a.keys \
        or jaro_winkler(a.full_key, b.full_key) >= _SAME_SPELLING


def _one_person(names: list[Name]) -> bool:
    distinct = sorted({n.full_key: n for n in names}.items())
    return all(same_person(a, b) for i, (_, a) in enumerate(distinct)
               for _, b in distinct[i + 1:])


class Sightings:
    """Where each code of a dataset is written, and with which patient."""

    def __init__(self, docs: Iterable[Document], lexicon: Lexicon) -> None:
        self.docs = list(docs)
        self.lexicon = lexicon
        self.veto = Veto(lexicon)
        self._people: dict[str, tuple[list[Name], bool]] = {}
        self._owners: dict[str, tuple[bool, str | None]] = {}
        self._protected: dict[int, list[tuple[int, int]]] = {}
        self._others: dict[int, tuple[list[int], list[Mention]]] = {}
        self.found: dict[str, list[Sighting]] = defaultdict(list)
        for doc in self.docs:
            for record in doc.records:
                for segment in record.segments:
                    if segment.channel in (Channel.HEADER, Channel.KEY) \
                            or segment.field is F.ATTACHMENT:
                        continue
                    text = segment.view.text
                    if not _DIGIT_RUN.search(text):
                        continue
                    for m in _TOKEN.finditer(text):
                        if _DIGIT_RUN.search(m.group(0)):
                            self.found[id_key(m.group(0))].append(
                                Sighting(record, segment, m.start(), m.group(0)))

    def protected(self, segment: Segment) -> list[tuple[int, int]]:
        """Facility, doctor and landmark names in a segment."""
        if id(segment) not in self._protected:
            self._protected[id(segment)] = self.veto.name_spans(segment.view.text)
        return self._protected[id(segment)]

    def other_kind(self, sighting: Sighting) -> bool:
        """Is this occurrence already another kind of data (an ABHA number, a date)?"""
        key = id(sighting.segment)
        if key not in self._others:
            # detected mentions of a segment are in order and never overlap
            others = [m for m in sighting.segment.mentions
                      if m.etype not in NAME_TYPES and m.etype not in (E.MRN, E.OTHER_ID)
                      and m.source != CLIPPED]
            self._others[key] = [m.start for m in others], others
        starts, others = self._others[key]
        index = bisect_left(starts, sighting.start + len(sighting.value))
        return index > 0 and others[index - 1].end > sighting.start

    def claimed(self, key: str) -> bool:
        return any(self.other_kind(s) for s in self.found.get(key, ()))

    def several_people(self, key: str) -> bool:
        """Is the value carried by people whom linking itself would tell apart?"""
        names = [n for s in self.found.get(key, ()) for p, several in [self.people(s.record)]
                 if not several for n in p]
        return len(name_groups(names)) > 1

    def people(self, record: Record) -> tuple[list[Name], bool]:
        """``(patient names, several people)`` of one record."""
        key = record.key
        if key not in self._people:
            names = [(n, m) for n, m in record_names(record, self.lexicon) if m.priority > 50]
            patient = [n for n, m in names if m.role is Role.PATIENT] \
                or [n for n, m in names if m.role is Role.UNKNOWN and m.source != "field"]
            self._people[key] = (patient, not _one_person(patient))
        return self._people[key]

    def owner(self, key: str) -> tuple[bool, str | None]:
        """``(shared, person)``: whether different people carry the value, else who does."""
        if key not in self._owners:
            written: dict[str, int] = defaultdict(int)
            records: dict[str, Record] = {}
            for sighting in self.found.get(key, ()):
                written[sighting.record.key] += 1
                records[sighting.record.key] = sighting.record
            names: list[Name] = []
            shared = False
            for record_key in sorted(records):
                patient, several = self.people(records[record_key])
                if several:
                    # in a list of several people, a repeated value belongs to several
                    shared = shared or written[record_key] > 1
                else:
                    names += patient
            shared = shared or not _one_person(names)
            person = max(names, key=lambda n: (n.size, n.full_key)).full_key \
                if names and not shared else None
            self._owners[key] = (shared, person)
        return self._owners[key]


def _declared_numbers(record: Record) -> set[str]:
    return {id_key(m.value) for _, m in record.mentions()
            if m.etype is E.MRN and m.source in ("field", "label", "layout")}


# ------------------------------------------------------------------- fields (route 1)


def _field_values(doc: Document) -> Iterable[tuple[str, Record, str]]:
    """``(label, record, value)`` for unfamiliar columns, keys and form labels."""
    columns: dict[str, list[tuple[Record, str]]] = defaultdict(list)
    for record in doc.records:
        for segment in record.segments:
            if segment.channel not in (Channel.CONTENT, Channel.ENCODED) or segment.numeric:
                continue
            text = segment.view.text
            if segment.field in (F.TEXT, F.NAME) and segment.label \
                    and doc.kind is not DocKind.FHIR and classify_label(segment.label) is None \
                    and text.strip():
                columns[normalize_label(segment.label)].append((record, text.strip()))
            if segment.field is F.TEXT and _DIGIT_RUN.search(text):
                for label, start, end in pairs(text):
                    if classify_label(label) is None:
                        yield normalize_label(label), record, text[start:end].strip()
    for label, cells in columns.items():
        shapes = [identifier_skeleton(value) for _, value in cells]
        # a column is typed only when nearly all of it has one skeleton
        for shape, count in Counter(s for s in shapes if s).items():
            if count >= 0.8 * len(shapes):
                for (record, value), found in zip(cells, shapes, strict=True):
                    if found == shape:
                        yield label, record, value


def field_numbers(sightings: Sightings) -> list[str]:
    """Values of unfamiliar fields that are patients' own numbers."""
    groups: dict[tuple[str, str], list[tuple[Record, str]]] = defaultdict(list)
    for doc in sightings.docs:
        for label, record, value in _field_values(doc):
            shape = identifier_skeleton(value)
            if shape:
                groups[label, shape].append((record, value))
    accepted: dict[str, str] = {}
    for (label, shape), found in sorted(groups.items()):
        cells, written = [], {}
        carriers: dict[str, list[Name]] = defaultdict(list)
        for record, value in found:
            patient, several = sightings.people(record)
            if not several:
                carriers[id_key(value)] += patient
        for record, value in found:
            key = id_key(value)
            shared, person = sightings.owner(key)
            shared = shared or not _one_person(carriers[key])
            patient, several = sightings.people(record)
            if person is None and patient and not several and not shared:
                person = patient[0].full_key       # written with spaces: not in the index
            written.setdefault(key, value)
            cells.append(NumberCell(key, record.key, person, shared,
                                    bool(_declared_numbers(record) - {key})))
        for key in personal_numbers(label, shape, cells):
            if not sightings.claimed(key):
                accepted.setdefault(key, written[key])
    return [accepted[key] for key in sorted(accepted)]


# ---------------------------------------------------------- co-occurrence (route 3)


def _anchor_shaped(value: str) -> bool:
    """Long enough to be somebody's number, and no date, code, measurement or product."""
    key = id_key(value)
    digits = sum(c.isdigit() for c in key)
    # a bare number could be an amount or a count: only a code with letters qualifies
    if not 6 <= len(key) <= 24 or not 4 <= digits < len(key) or reads_as_other(value) \
            or EVENT_PREFIX.match(value):
        return False
    # "Azee-1000" is a product; identifier prefixes are capitals or come in several parts
    return value == value.upper() or sum(not c.isalnum() for c in value) >= 2


def _free_standing(sightings: Sightings, sighting: Sighting) -> bool:
    """Nothing around this occurrence says the code is something other than a person's."""
    segment, start = sighting.segment, sighting.start
    if segment.field not in _OPEN_FIELDS:
        return False
    if segment.label:
        words = label_words(" ".join(normalize_label(segment.label).split()))
        if classify_label(segment.label) not in (None, *_OPEN_FIELDS) or foreign_number(words):
            return False
    text = segment.view.text
    end = start + len(sighting.value)
    if foreign_number(words_before(text, start)) \
            or text[max(0, start - 3):start].rstrip().endswith("₹"):
        return False
    if sightings.other_kind(sighting):
        return False
    return not any(a < end and start < b for a, b in sightings.protected(segment))


def _family(value: str) -> str:
    return re.sub(r"\d", "#", id_key(value))


def repeated_numbers(sightings: Sightings) -> list[str]:
    """Codes written with the same patient's name in two or more files.

    Rows of one table repeating a code (the tests of one specimen) are not enough: a
    patient's own number is met again elsewhere.
    """
    candidates = {key: found for key, found in sorted(sightings.found.items())
                  if len({s.record.doc.rel for s in found}) >= 2
                  and _anchor_shaped(found[0].value)}
    # codes of a kind that different people share (ward or package codes) are all codes
    members: dict[str, int] = defaultdict(int)
    shared: dict[str, int] = defaultdict(int)
    for key, found in candidates.items():
        family = _family(found[0].value)
        members[family] += 1
        shared[family] += sightings.owner(key)[0]
    accepted = []
    for key, found in candidates.items():
        family = _family(found[0].value)
        if sightings.owner(key)[1] is None or shared[family] * 5 > members[family]:
            continue
        named = {s.record.key for s in found
                 if (p := sightings.people(s.record))[0] and not p[1]}
        if len({record_key.split("\x00")[0] for record_key in named}) >= 2 and all(_free_standing(sightings, s) for s in found):
            accepted.append(found[0].value)
    return accepted


# ------------------------------------------------------------------- labelled numbers


def demote_shared(sightings: Sightings) -> None:
    """A number found by a generic label but carried by different people is still removed,
    but it must never join their records."""
    for doc in sightings.docs:
        for segment in doc.segments:
            for mention in segment.mentions:
                if mention.etype is E.MRN and mention.data == NUMBER_LABEL \
                        and sightings.several_people(id_key(mention.value)):
                    mention.etype = E.OTHER_ID


def declared_numbers(sightings: Sightings) -> list[tuple[str, E]]:
    """Labelled and field numbers of one person each, to be searched for everywhere.

    A value that any rule reads as a hospital number is searched for as one; a value that is
    also another kind of data (an insurance number) keeps what its own rules call it.
    """
    found: dict[str, tuple[str, E]] = {}
    for doc in sightings.docs:
        for segment in doc.segments:
            for m in segment.mentions:
                generic = m.etype is E.OTHER_ID and m.data == NUMBER_LABEL
                if not (m.etype is E.MRN or generic) or m.source not in _DECLARED:
                    continue
                key = id_key(m.value)
                if key in found and generic:
                    continue
                if not sightings.owner(key)[0] and not sightings.claimed(key):
                    found[key] = (m.value, m.etype)
    return [found[key] for key in sorted(found)]

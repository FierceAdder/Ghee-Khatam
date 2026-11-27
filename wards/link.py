"""Recognise the same patient across files, languages and hospitals.

Linking is precision first and mirrors the published truth rule: records are joined only
by a strong identifier, or by the same name *plus* a second clue whose combination is
unique. A union-find keeps a summary per cluster and refuses any merge that would put two
Aadhaar numbers, two sexes, impossible ages or two different given names in one patient.
Two narrower tiers run last: a note that carries a patient's registered phone and a form of
their name joins that patient, and records of one file that show they are about one person
(a shared specimen or visit number, or the same full name, age and sex) form a local group.
Every iteration is in sorted order, so the result is deterministic.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from functools import lru_cache

from wards.columns import normalize_label
from wards.detect.demographics import normalize_sex, sexes_in_text
from wards.knowledge import place_key
from wards.lexicon import GENERIC_NAME_TOKENS, HONORIFICS_DEVANAGARI, HONORIFICS_LATIN, Lexicon
from wards.model import CLIPPED, NAME_TYPES, Mention, Record, Role
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import digits_only, id_key, phone_key, phonetic_key
from wards.security import Secret
from wards.textview import TextView

PSEUDONYM_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"     # Crockford base32
PSEUDONYM_PREFIX = "PSN-"
PSEUDONYM_LENGTH = 6

# Order in which strong identifiers are applied: the most specific first.
# Hospital and insurance numbers share one kind: a label can call the same value either
# ("Card No"), and the value, not the label, is what makes it unique.
_STRONG_ORDER = ("aadhaar", "abha", "hid", "pan", "email", "phone")
_NAME_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[\u0900-\u0963\u0971-\u097f]+")
_DEVA_HONORIFICS = frozenset(TextView.of(w).text for w in HONORIFICS_DEVANAGARI)
_RELATION_SEX = re.compile(r"(?i)(?<![A-Za-z/])(?P<r>[sdw])[ \t]*/[ \t]*o(?![A-Za-z])")
# A title that says whether the person named after it is a man or a woman.
_TITLED = tuple(
    (sex, re.compile(r"(?<![A-Za-z\u0900-\u097f])(?:" + unicodedata.normalize("NFKC", words)
                     + r")[. \t]*$", re.IGNORECASE))
    for sex, words in (("M", "mr|mstr|master|shri|श्री"),
                       ("F", "mrs|ms|miss|smt|km|kumari|श्रीमती|सुश्री|कुमारी")))
# Parts of an address that two records of one person would not disagree on.
_ADDRESS_PARTS = frozenset({E.HOUSE, E.LOCALITY, E.TEHSIL, E.PIN})
# Column headers and JSON keys of the numbers that name one sample or one visit.
_LOCAL_NUMBERS = tuple((kind, re.compile(pattern)) for kind, pattern in (
    ("specimen", r" (?:sample|specimen|accession) (?:id|no|num|number|barcode) "),
    ("visit", r" (?:visit|encounter) (?:id|no|num|number) "),
    ("specimen", r" (?:नमूना|सैंपल) (?:सं|संख्या|क्रमांक|नंबर|आईडी) "),
))


@dataclass(frozen=True)
class Name:
    keys: frozenset[str]
    given: frozenset[str]
    size: int
    # The words in order, for compound names ("Ram Kumar", "Ramkumar").
    spelled: tuple[str, ...] = ()           # the identifying words
    written: tuple[str, ...] = ()           # with the honorifics left in

    @property
    def full(self) -> bool:
        return self.size >= 2

    @property
    def full_key(self) -> str:
        return "+".join(sorted(self.keys))


def name_tokens(value: str, lexicon: Lexicon) -> list[str]:
    """Identifying words of a name: titles, honorifics and initials removed."""
    words = []
    for word in _NAME_WORD.findall(value):
        low = word.lower()
        if len(word) < 2 or low in HONORIFICS_LATIN or word in _DEVA_HONORIFICS:
            continue
        if lexicon.is_stopword(word) and not lexicon.is_known_name(word):
            continue
        words.append(word)
    return words


def parse_name(value: str, lexicon: Lexicon) -> Name | None:
    words = name_tokens(value, lexicon)
    pairs = [(w, lexicon.name_key(w)) for w in words]
    pairs = [(w, k) for w, k in pairs if k]
    if not pairs:
        return None
    core = [(w, k) for w, k in pairs if not lexicon.is_generic(w)] or pairs
    if len(core) >= 2 and lexicon.is_surname(core[-1][0]):
        core = core[:-1]
    # Honorific words stay in "written": "Babu" is part of the name "Ram Babu".
    return Name(keys=frozenset(k for _, k in pairs), given=frozenset(k for _, k in core),
                size=len({k for _, k in pairs}),
                spelled=tuple(lexicon.canonical(w) for w, _ in pairs),
                written=tuple(lexicon.canonical(w) for w in _NAME_WORD.findall(value)
                              if len(w) >= 2))


@lru_cache(maxsize=65_536)
def _runs_into(part: tuple[str, ...], whole: tuple[str, ...]) -> bool:
    """All of ``part`` is found, word for word, in adjacent words of ``whole``, where at
    least once several words on one side are written as one word on the other."""

    def match(i: int, j: int, joined: bool) -> bool:
        if i == len(part):
            return joined
        for m in range(1, len(part) - i + 1):
            for n in range(1, len(whole) - j + 1):
                if min(m, n) == 1 \
                        and phonetic_key("".join(part[i:i + m])) \
                        == phonetic_key("".join(whole[j:j + n])) \
                        and match(i + m, j + n, joined or m + n > 2):
                    return True
        return False

    if len(part) == 1 and len(whole) == 1:
        return False
    head = phonetic_key(part[0])[:1]        # a cheap test before trying the splits
    return any(match(0, start, False) for start in range(len(whole))
               if phonetic_key(whole[start])[:1] == head)


def joined_match(a: Name, b: Name) -> bool:
    """One name is the other written with words run together or split apart: all of one is
    found in adjacent words of the other. Words that are not joined must still agree, so a
    surname that differs still differs."""
    return any(_runs_into(part, whole)
               for x, y in ((a, b), (b, a))
               for part in (x.spelled, x.written) if part
               for whole in (y.spelled, y.written))


def same_name(a: Name, b: Name) -> bool:
    """One name is the other, or a shorter form of it (stricter than compatible)."""
    return a.keys <= b.keys or b.keys <= a.keys or joined_match(a, b)


def names_compatible(a: Name, b: Name) -> bool:
    return a.keys <= b.keys or b.keys <= a.keys or bool(a.given & b.given) \
        or joined_match(a, b)


def _name_order(name: Name) -> tuple:
    """A total order on names, so that nothing depends on how a set happens to iterate."""
    return -name.size, sorted(name.keys), sorted(name.given), name.spelled, name.written


def record_names(record: Record, lexicon: Lexicon) -> list[tuple[Name, Mention]]:
    """The names in a record, with split first/last fields joined into one name."""
    found: list[tuple[Name, Mention]] = []
    parts: dict[Role, list[tuple[bool, Mention]]] = defaultdict(list)
    for segment, mention in record.mentions():
        # staff are not the patient and not a clue to who the patient is
        if mention.etype not in NAME_TYPES or mention.source in (CLIPPED, "model") \
                or mention.role is Role.STAFF:
            continue
        if segment.field in (F.FIRST_NAME, F.LAST_NAME) and mention.source == "field":
            parts[mention.role].append((segment.field is F.LAST_NAME, mention))
            continue
        name = parse_name(mention.value, lexicon)
        if name is not None:
            found.append((name, mention))
    for role, items in parts.items():
        ordered = [m for _, m in sorted(items, key=lambda pair: pair[0])]
        value = " ".join(m.value for m in ordered)
        name = parse_name(value, lexicon)
        if name is not None:
            first = ordered[0]
            found.append((name, Mention(first.etype, first.start, first.end, value, role=role,
                                        priority=first.priority, source="field")))
    return found


def name_groups(names: Iterable[Name]) -> list[list[Name]]:
    """Group names that could belong to one person."""
    groups: list[list[Name]] = []
    for name in sorted(names, key=_name_order):
        for group in groups:
            if any(names_compatible(name, other) for other in group):
                group.append(name)
                break
        else:
            groups.append([name])
    return groups


@dataclass
class Summary:
    """Everything known about one record, or about one cluster of records."""

    strong: set[tuple[str, str]] = field(default_factory=set)
    names: set[Name] = field(default_factory=set)
    relatives: set[Name] = field(default_factory=set)
    dobs: set[dt.date] = field(default_factory=set)
    sexes: set[str] = field(default_factory=set)
    births: set[tuple[int, int]] = field(default_factory=set)      # birth year ranges
    villages: set[str] = field(default_factory=set)

    def absorb(self, other: "Summary") -> None:
        for name in ("strong", "names", "relatives", "dobs", "sexes", "births", "villages"):
            getattr(self, name).update(getattr(other, name))

    def values(self, kind: str) -> set[str]:
        return {v for k, v in self.strong if k == kind}

    def birth_gap(self, other: "Summary") -> int | None:
        """Smallest distance in years between the two sets of birth-year estimates."""
        if not self.births or not other.births:
            return None
        return min(max(0, a[0] - b[1], b[0] - a[1]) for a in self.births for b in other.births)


def conflict(a: Summary, b: Summary) -> str | None:
    """Why ``a`` and ``b`` cannot be one person (``sex``, ``aadhaar``…), or ``None``."""
    if a.sexes and b.sexes and not a.sexes & b.sexes:
        return "sex"
    for kind in ("aadhaar", "abha"):
        left, right = a.values(kind), b.values(kind)
        if left and right and not left & right:
            return kind
    if a.dobs and b.dobs and not a.dobs & b.dobs:
        # tolerate only a swapped day and month
        swapped = any(x.year == y.year and x.month == y.day and x.day == y.month
                      for x in a.dobs for y in b.dobs)
        if not swapped:
            return "dob"
    gap = a.birth_gap(b)
    if gap is not None and gap > 2:
        return "age"
    if a.names and b.names and not any(names_compatible(x, y) for x in a.names for y in b.names):
        return "name"
    return None


def compatible(a: Summary, b: Summary) -> bool:
    return conflict(a, b) is None


@dataclass
class _Profile:
    """What records that may form a local group say, to test that they agree."""

    summary: Summary = field(default_factory=Summary)
    names: set[Name] = field(default_factory=set)
    ages: set[int] = field(default_factory=set)
    address: set[tuple[str, str]] = field(default_factory=set)

    @classmethod
    def of(cls, item: "Evidence") -> "_Profile":
        profile = cls()
        profile.summary.absorb(item.summary)
        profile.names.update(item.summary.names or item.mentioned)
        profile.ages.update(item.ages)
        profile.address.update(item.address)
        return profile

    def absorb(self, other: "_Profile") -> None:
        self.summary.absorb(other.summary)
        self.names.update(other.names)
        self.ages.update(other.ages)
        self.address.update(other.address)


def local_conflict(a: _Profile, b: _Profile) -> str | None:
    """Why two records of one file cannot be grouped. Stricter than :func:`conflict`:
    local evidence is thin, so any disagreement keeps them apart."""
    reason = conflict(a.summary, b.summary)
    if reason is not None:
        return reason
    if not all(same_name(x, y) for x in a.names for y in b.names):
        return "name"
    for kind in ("hid", "pan", "email", "phone"):
        left, right = a.summary.values(kind), b.summary.values(kind)
        if left and right and not left & right:
            return kind
    if a.ages and b.ages and not a.ages & b.ages:
        return "age"
    if a.summary.villages and b.summary.villages \
            and not a.summary.villages & b.summary.villages:
        return "village"
    for part in sorted({kind for kind, _ in a.address} & {kind for kind, _ in b.address}):
        if not {v for k, v in a.address if k == part} & {v for k, v in b.address if k == part}:
            return "address"
    if a.summary.relatives and b.summary.relatives and not any(
            names_compatible(x, y) for x in a.summary.relatives for y in b.summary.relatives):
        return "relative"
    return None


def _identified(summary: Summary) -> bool:
    """Carries an identifier of its own (a phone may belong to somebody else)."""
    return any(kind != "phone" for kind, _ in summary.strong)


def _is_note(item: "Evidence") -> bool:
    """Not a patient's own record: it has no identifier and no phone field."""
    return not _identified(item.summary) and not item.registered


def _titled_sexes(record: Record) -> frozenset[str]:
    """What "Mr." or "Smt." before a name that may be the patient's says about sex."""
    found = set()
    for segment, mention in record.mentions():
        if mention.etype in NAME_TYPES and mention.role not in (Role.RELATIVE, Role.STAFF):
            before = segment.view.text[:mention.start][-12:]
            found.update(sex for sex, title in _TITLED if title.search(before))
    return frozenset(found)


def _local_numbers(record: Record) -> frozenset[tuple[str, str]]:
    found = set()
    for segment in record.segments:
        if not segment.label:
            continue
        label = normalize_label(segment.label)
        for kind, pattern in _LOCAL_NUMBERS:
            if pattern.search(label):
                value = id_key(segment.text)
                digits = digits_only(value)
                # "1", "2" count visits and "00000" is a blank: neither names one
                if len(value) >= 5 and len(digits) >= 3 and len(set(digits)) > 1:
                    found.add((kind, value))
    return frozenset(found)


@dataclass
class Evidence:
    record: Record
    summary: Summary
    anchored: bool          # carries something that identifies a patient
    ambiguous: bool         # describes several people
    mentioned: tuple[Name, ...] = ()    # any name that may be the patient's, short ones too
    registered: frozenset[str] = frozenset()        # phones held in a phone field
    numbers: frozenset[tuple[str, str]] = frozenset()       # specimen and visit numbers
    ages: frozenset[int] = frozenset()
    days: frozenset[dt.date] = frozenset()          # dates other than a birth date
    titled: frozenset[str] = frozenset()            # sexes the titles before names imply
    address: frozenset[tuple[str, str]] = frozenset()


class Clusters:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.parent = list(range(len(evidence)))
        self.summary = []
        for item in evidence:
            summary = Summary()
            summary.absorb(item.summary)
            self.summary.append(summary)

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        root, child = min(a, b), max(a, b)
        self.parent[child] = root
        self.summary[root].absorb(self.summary[child])


class Linker:
    def __init__(self, lexicon: Lexicon, secret: Secret, phone_hub_groups: int = 3,
                 fuzzy: bool = False) -> None:
        self.lexicon = lexicon
        self.secret = secret
        self.hub = phone_hub_groups
        self.fuzzy = fuzzy
        self.generic = frozenset(lexicon.name_key(w) for w in GENERIC_NAME_TOKENS)
        self.stats: Counter = Counter()     # decisions, for the log (counts only)
        self.local: dict[int, str] = {}     # evidence index -> key of its file-local group
        self.unclaimed: set[int] = set()    # grouped on evidence too thin to report

    # ------------------------------------------------------------------ evidence

    def evidence(self, record: Record, order: str, reference_year: int) -> Evidence:
        summary = Summary()
        patient, unknown, relatives = [], [], []
        ages, years, days = [], [], []
        registered, address = set(), set()
        for name, mention in record_names(record, self.lexicon):
            if mention.role is Role.RELATIVE:
                relatives.append(name)
            elif mention.role is Role.PATIENT:
                patient.append(name)
            elif mention.source != "field":     # a "caller" or "witness" field is not one
                unknown.append(name)
        for _, mention in record.mentions():
            etype = mention.etype
            if etype in NAME_TYPES or mention.source == CLIPPED:
                continue
            if etype in (E.DATE, E.DOB):
                value = mention.data.resolve(order, reference_year) if mention.data else None
                if value is None:
                    continue
                if etype is E.DOB:
                    summary.dobs.add(value)
                else:
                    days.append(value)
                    if mention.data.year is not None:
                        years.append(value.year)
            elif etype is E.AGE:
                if mention.data != "infant" and mention.value.isdigit():
                    ages.append(int(mention.value))
                else:
                    ages.append(0)
            elif etype is E.VILLAGE:
                key = place_key(mention.value)
                if key:
                    summary.villages.add(key)
            elif etype in _ADDRESS_PARTS:
                address.add((etype.value, " ".join(mention.value.casefold().split())))
            else:
                strong = _strong_key(mention)
                if strong:
                    summary.strong.add(strong)
                    if strong[0] == "phone" and mention.source == "field":
                        registered.add(strong[1])

        mentioned = tuple(patient + self._not_relatives(unknown, relatives))
        patient = self._patient_names(patient, unknown, relatives)
        summary.names.update(patient)
        summary.relatives.update(relatives)
        summary.sexes.update(self._sexes(record))
        for dob in summary.dobs:
            summary.births.add((dob.year, dob.year))
        if not summary.dobs:
            low_year = min(years) if years else reference_year - 1
            high_year = max(years) if years else reference_year
            for age in ages:
                summary.births.add((low_year - age - 1, high_year - age))

        groups = name_groups(summary.names)
        shapes: dict[str, set[str]] = defaultdict(set)
        for value in summary.values("hid"):
            shapes[re.sub(r"\d", "#", value)].add(value)
        ambiguous = len(groups) > 1 or len(summary.values("aadhaar")) > 1 \
            or len(summary.values("abha")) > 1 or any(len(v) > 2 for v in shapes.values())
        # A phone alone may belong to a visitor or caller, so it anchors nothing.
        anchored = bool(summary.names) or any(k != "phone" for k, _ in summary.strong)
        return Evidence(record, summary, anchored, ambiguous, mentioned=mentioned,
                        registered=frozenset(registered), numbers=_local_numbers(record),
                        ages=frozenset(ages), days=frozenset(days),
                        titled=_titled_sexes(record), address=frozenset(address))

    @staticmethod
    def _not_relatives(unknown: list[Name], relatives: list[Name]) -> list[Name]:
        return [n for n in unknown
                if not any(n.keys <= r.keys or r.keys <= n.keys for r in relatives)]

    @classmethod
    def _patient_names(cls, patient: list[Name], unknown: list[Name],
                       relatives: list[Name]) -> list[Name]:
        others = cls._not_relatives(unknown, relatives)
        if patient:
            # unlabelled repetitions of the same patient add detail (e.g. the surname)
            return patient + [n for n in others
                              if any(names_compatible(n, p) for p in patient)]
        full = [n for n in others if n.full]
        return full if len(name_groups(full)) == 1 else []

    def _sexes(self, record: Record) -> set[str]:
        found: set[str] = set()
        for segment in record.segments:
            if segment.field is F.SEX:
                sex = normalize_sex(segment.view.text)
                if sex:
                    found.add(sex)
            else:
                found |= sexes_in_text(segment.view.text)
        if found:
            return found if len(found) == 1 else set()
        for segment in record.segments:
            for m in _RELATION_SEX.finditer(segment.view.text):
                found.add("M" if m.group("r").lower() == "s" else "F")
        return found if len(found) == 1 else set()

    # ------------------------------------------------------------------- linking

    def link(self, evidence: list[Evidence]) -> dict[int, str]:
        """Pseudonym for each evidence index that identifies a patient. Afterwards
        ``local`` and ``unclaimed`` describe the file-local groups."""
        self.stats = Counter()
        self.local, self.unclaimed = {}, set()
        if self.fuzzy:
            _unify_spellings(evidence)
        evidence_order = sorted(range(len(evidence)), key=lambda i: evidence[i].record.key)
        usable = [i for i in evidence_order
                  if evidence[i].anchored and not evidence[i].ambiguous]
        self.stats.update(records=len(evidence),
                          ambiguous=sum(e.ambiguous for e in evidence),
                          unanchored=sum(not e.anchored for e in evidence))
        clusters = Clusters(evidence)
        hubs, torn = self._strong_tier(evidence, usable, clusters)
        set_aside = [i for i in evidence_order
                     if evidence[i].anchored and evidence[i].ambiguous]
        for name, tier in (("dob", self._name_dob_keys), ("relative", self._name_relative_keys),
                           ("village", self._name_village_keys),
                           ("given", self._given_relative_keys)):
            self._unique_tier(evidence, usable, clusters, tier, name, set_aside)
        torn |= self._phone_tier(evidence, usable, clusters, set_aside, hubs)
        self.stats["skipped_shared_phone"] += len(torn - hubs)    # hubs are counted already
        # last, so that nothing local is ever used to link further
        groups = self._local_tier(evidence, evidence_order, clusters)

        members: dict[int, list[int]] = defaultdict(list)
        for index in usable:
            members[clusters.find(index)].append(index)
        assigned = self._pseudonyms(evidence, clusters, members)
        for group, thin in groups:
            # one shift for the whole group, also for rows that name nobody
            key = next((assigned[i] for i in group if i in assigned),
                       evidence[group[0]].record.key)
            self.local.update(dict.fromkeys(group, key))
            self.unclaimed.update(i for i in thin if i in assigned)
        self.stats.update(linked=len(assigned), patients=len(set(assigned.values())))
        return assigned

    def _strong_tier(self, evidence: list[Evidence], usable: list[int],
                     clusters: Clusters) -> tuple[set[str], set[str]]:
        """Join records that share a strong identifier. Return the phones that too many
        people share to identify any of them, and those that left a note undecided."""
        hubs: set[str] = set()
        torn: set[str] = set()
        index: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i in usable:
            for key in sorted(evidence[i].summary.strong):
                index[key].append(i)
        for kind in _STRONG_ORDER:
            for key in sorted(k for k in index if k[0] == kind):
                group = index[key]
                if kind == "phone":
                    names = [n for i in group for n in evidence[i].summary.names]
                    if len(name_groups(names)) >= self.hub:
                        self.stats["skipped_shared_phone"] += 1
                        hubs.add(key[1])
                        continue        # a shared desk or family number identifies nobody
                    # patients' own records first, then notes with the fuller name first,
                    # and records with a name before anonymous ones
                    group = sorted(group, key=lambda i: (
                        _is_note(evidence[i]), not evidence[i].summary.names,
                        -max((n.size for n in evidence[i].summary.names), default=0)
                        if _is_note(evidence[i]) else 0, evidence[i].record.key))
                representatives: list[int] = []
                for i in group:
                    if kind == "phone" and _is_note(evidence[i]) \
                            and self._torn(i, representatives, clusters):
                        torn.add(key[1])
                        continue        # "Verma ji" and two Vermas share the number
                    refusal = None
                    for other in representatives:
                        a, b = clusters.find(i), clusters.find(other)
                        reason = None if a == b else \
                            conflict(clusters.summary[a], clusters.summary[b])
                        if reason is None:
                            self.stats["merged_strong"] += a != b
                            clusters.union(a, b)
                            break
                        refusal = refusal or reason
                    else:
                        if refusal:
                            self.stats["refused_" + refusal] += 1
                        representatives.append(i)
        return hubs, torn

    @staticmethod
    def _torn(note: int, representatives: list[int], clusters: Clusters) -> bool:
        """Could the note join patients with different names? Several record sets of one
        name do not count: the note then joins the first, as for any shared identifier."""
        mine = clusters.find(note)
        fits = sorted({root for other in representatives for root in [clusters.find(other)]
                       if root == mine
                       or compatible(clusters.summary[mine], clusters.summary[root])})
        names = sorted({n for root in fits for n in clusters.summary[root].names},
                       key=_name_order)
        return len(fits) > 1 and len(name_groups(names)) > 1

    def _unique_tier(self, evidence, usable, clusters: Clusters, keys_of, name: str,
                     set_aside: list[int] = ()) -> None:
        index: dict[tuple, list[int]] = defaultdict(list)
        for i in usable:
            for key in sorted(keys_of(clusters.summary[clusters.find(i)], evidence[i].summary)):
                index[key].append(i)
        # A clue is unique only if no other record carries it, including records set aside
        # as describing several people: they still show that someone else has it.
        elsewhere = {key for i in set_aside
                     for key in keys_of(evidence[i].summary, evidence[i].summary)}
        for key in sorted(index):
            if key in elsewhere:
                self.stats["skipped_weak"] += 1
                continue
            roots = sorted({clusters.find(i) for i in index[key]})
            if len(roots) < 2:
                continue
            summaries = [clusters.summary[r] for r in roots]
            pairs = [(a, b) for n, a in enumerate(summaries) for b in summaries[n + 1:]]
            if not all(compatible(a, b) for a, b in pairs):
                self.stats["skipped_conflict"] += 1
                continue                # the clue fits different people: do not guess
            if key[0] == "village":
                gaps = [a.birth_gap(b) for a, b in pairs]
                if any(g is None or g > 1 for g in gaps):
                    self.stats["skipped_weak"] += 1
                    continue
            if key[0] == "given" and not all(
                    any(x.keys <= y.keys or y.keys <= x.keys for x in a.names for y in b.names)
                    for a, b in pairs):
                self.stats["skipped_weak"] += 1
                continue
            self.stats["merged_" + name] += len(roots) - 1
            for root in roots[1:]:
                clusters.union(roots[0], root)

    # Clue keys use the record's own name together with clues known for its cluster.

    @staticmethod
    def _name_dob_keys(cluster: Summary, own: Summary):
        return {("dob", n.full_key, d.isoformat())
                for n in own.names if n.full for d in cluster.dobs}

    @staticmethod
    def _name_relative_keys(cluster: Summary, own: Summary):
        return {("relative", n.full_key, key)
                for n in own.names if n.full for r in cluster.relatives for key in r.given}

    @staticmethod
    def _name_village_keys(cluster: Summary, own: Summary):
        return {("village", n.full_key, village)
                for n in own.names if n.full for village in cluster.villages}

    @staticmethod
    def _given_relative_keys(cluster: Summary, own: Summary):
        """"Imli, W/o Temjen": a first name and the relative's name together."""
        return {("given", given, key)
                for n in own.names for given in n.given
                for r in cluster.relatives for key in r.given}

    # -------------------------------------------------------------- phone and name

    def _phone_tier(self, evidence: list[Evidence], usable: list[int], clusters: Clusters,
                    set_aside: list[int], hubs: set[str]) -> set[str]:
        """A patient's registered phone in a note that also names them, even by surname
        alone, links the note, whoever the text says the number belongs to. Only notes with
        no identifier of their own move, and only when exactly one patient fits. Returns
        the registered phones that named people could not be told apart by."""
        held: dict[str, set[int]] = defaultdict(set)
        for i in usable:
            for phone in evidence[i].summary.values("phone"):
                held[phone].add(clusters.find(i))
        full = {clusters.find(i) for i in usable
                if any(n.full for n in clusters.summary[clusters.find(i)].names)}
        registrations = set()           # (patient, phone) from a field or an identified record
        for i in usable:
            item, root = evidence[i], clusters.find(i)
            if root in full:
                phones = item.summary.values("phone") if _identified(item.summary) \
                    else item.registered
                registrations.update((root, phone) for phone in phones)
        members: dict[int, list[int]] = defaultdict(list)
        for i, item in enumerate(evidence):
            if not item.ambiguous:
                members[clusters.find(i)].append(i)

        linking = set(usable)
        joins: list[tuple[int, int]] = []
        shared: set[str] = set()
        for root in sorted(members, key=lambda r: min(evidence[i].record.key
                                                      for i in members[r])):
            notes = members[root]
            if not all(_is_note(evidence[i]) for i in notes):
                continue
            phones = sorted({p for i in notes for p in evidence[i].summary.values("phone")}
                            - hubs)
            known = [(r, p) for p in phones for r in sorted(held[p] - {root})
                     if (r, p) in registrations]
            if not known:
                continue
            fits = sorted({r for p in phones for r in held[p] - {root} if r in full and all(
                self._names_patient(evidence[i], clusters.summary[r]) for i in notes)})
            if len(fits) == 1 and any(r == fits[0] for r, _ in known) \
                    and not self._fits_set_aside(evidence, notes, set_aside, phones,
                                                 clusters.summary[fits[0]].names):
                joins.append((root, fits[0]))
            elif any(evidence[i].summary.names or evidence[i].mentioned for i in notes):
                shared.update(p for _, p in known)
        # decided above on the patients as they stood, so that one note cannot vouch for another
        for root, patient in joins:
            a, b = clusters.find(root), clusters.find(patient)
            reason = conflict(clusters.summary[a], clusters.summary[b])
            if reason is not None:
                self.stats["refused_" + reason] += 1
                continue
            self.stats["merged_phone"] += 1
            clusters.union(a, b)
            usable.extend(i for i in members[root] if i not in linking)
        return shared

    def _short_for(self, name: Name, patient: Name) -> bool:
        """``name`` is ``patient``'s name or a short form of it, and says who: "Devi" or
        "Kumar" alone does not."""
        telling = name.keys - self.generic
        return bool(telling) and telling <= patient.keys or joined_match(name, patient)

    def _names_patient(self, note: Evidence, patient: Summary,
                       names: set[Name] | None = None) -> bool:
        """The note names this patient, and names no other patient."""
        if note.titled and patient.sexes and not note.titled <= patient.sexes:
            return False                # "Mr. Verma" is not the woman who has the phone
        names = patient.names if names is None else names

        def fits(name: Name) -> bool:
            return any(self._short_for(name, other) for other in names)

        return all(fits(n) or any(n.keys <= other.keys for other in names)
                   for n in note.summary.names) \
            and all(fits(n) for n in note.mentioned if n.full) \
            and any(fits(n) for n in (*note.summary.names, *note.mentioned))

    def _fits_set_aside(self, evidence: list[Evidence], notes: list[int],
                        set_aside: list[int], phones: list[str], names: set[Name]) -> bool:
        """A record about several people shows the same phone with another person whom the
        note could mean just as well."""
        others = [n for i in set_aside if evidence[i].summary.values("phone") & set(phones)
                  for n in sorted(evidence[i].summary.names, key=_name_order)
                  if n.full and not any(names_compatible(n, m) for m in names)]
        return any(all(self._names_patient(evidence[i], Summary(), {other}) for i in notes)
                   for other in others)

    # ------------------------------------------------------------ file-local groups

    def _local_tier(self, evidence: list[Evidence], order: list[int],
                    clusters: Clusters) -> list[tuple[list[int], list[int]]]:
        """Group records of one file that nothing else linked. Returns each group with the
        members whose tie to it is too thin to report in ``records.jsonl``.

        A shared specimen or visit number, or the same full name, age and sex, puts records
        in one group. If any two records of the file that carry the same clue disagree, the
        clue does not name one person and is not used at all. Name, age and sex on different
        dates still give one pseudonym, but only the records tied by a number or a shared
        date are reported under it: a homonym must not be claimed as one patient."""
        size = Counter(clusters.find(i) for i in range(len(evidence)))
        free = {i for i in order
                if not evidence[i].ambiguous and size[clusters.find(i)] == 1}
        numbers: dict[tuple, list[int]] = defaultdict(list)
        people: dict[tuple, list[int]] = defaultdict(list)
        for i in order:
            item = evidence[i]
            file = item.record.doc.rel
            for kind, value in item.numbers:
                numbers[file, kind, value].append(i)
            if len(item.ages) == 1 and len(item.summary.sexes) == 1:
                about = (min(item.ages), min(item.summary.sexes))
                for key in {k for n in item.summary.names if n.full and n.keys - self.generic
                            for k in (n.full_key, phonetic_key("".join(n.spelled)))}:
                    people[(file, key, *about)].append(i)

        profiles: dict[int, _Profile] = {}      # group root -> what its records say
        grouped: set[int] = set()
        sure: list[list[int]] = []              # records tied by evidence worth reporting
        for index, strong in ((numbers, True), (people, False)):
            for key in sorted(index):
                found = index[key]
                if len(found) < 2 or not any(i in free for i in found):
                    continue
                if not _agree(evidence, found):
                    self.stats["skipped_conflict"] += 1
                    continue
                roots: list[int] = []
                for i in (i for i in found if i in free):
                    mine = clusters.find(i)
                    profiles.setdefault(mine, _Profile.of(evidence[i]))
                    for other in roots:
                        a, b = clusters.find(mine), clusters.find(other)
                        if a == b:
                            break
                        if local_conflict(profiles[a], profiles[b]) is None:
                            self.stats["merged_local"] += 1
                            clusters.union(a, b)
                            profiles[min(a, b)].absorb(profiles[max(a, b)])
                            grouped.update((i, other))
                            break
                    else:
                        roots.append(mine)
                together: dict[object, list[int]] = defaultdict(list)
                for i in (i for i in found if i in free):
                    # a number ties its records; a name does so only on a shared date
                    for day in [None] if strong else sorted(evidence[i].days):
                        together[clusters.find(i), day].append(i)
                sure.extend(together.values())

        tied = Clusters(evidence)
        for records in sure:
            for i in records[1:]:
                tied.union(records[0], i)
        groups: dict[int, list[int]] = defaultdict(list)
        roots = {clusters.find(i) for i in grouped}
        for i in order:
            if i in free and clusters.find(i) in roots:
                groups[clusters.find(i)].append(i)
        return [(group, _beyond_best(evidence, group, tied)) for group in groups.values()]

    # ---------------------------------------------------------------- pseudonyms

    def _pseudonyms(self, evidence: list[Evidence], clusters: Clusters,
                    members: dict[int, list[int]]) -> dict[int, str]:
        keyed = []
        for root, indices in members.items():
            summary = clusters.summary[root]
            strong = sorted(f"{k}:{v}" for k, v in summary.strong if k != "phone")
            identified = bool(strong) or len(indices) > 1 \
                or any(n.full for n in summary.names)
            if not identified:
                continue                # a thin mention such as a lone first name
            first = min(evidence[i].record.key for i in indices)
            keyed.append(("|".join(strong) or first, first, indices))
        assigned: dict[int, str] = {}
        taken: set[str] = set()
        for cluster_key, _, indices in sorted(keyed):
            attempt = 0
            pseudonym = self.pseudonym(cluster_key)
            while pseudonym in taken:
                attempt += 1
                pseudonym = self.pseudonym(f"{cluster_key}#{attempt}")
            taken.add(pseudonym)
            for index in indices:
                assigned[index] = pseudonym
        return assigned

    def pseudonym(self, cluster_key: str) -> str:
        number = int.from_bytes(self.secret.mac("pseudonym", cluster_key)[:5], "big")
        code = "".join(PSEUDONYM_ALPHABET[(number >> (5 * i)) & 31]
                       for i in range(PSEUDONYM_LENGTH))
        return PSEUDONYM_PREFIX + code


def _beyond_best(evidence: list[Evidence], group: list[int], tied: Clusters) -> list[int]:
    """The members outside the tied part of a group that names the patient most often (the
    earliest of equals)."""
    parts: dict[int, list[int]] = defaultdict(list)
    for i in group:
        parts[tied.find(i)].append(i)
    # parts are in record order, and max() takes the first of equals
    best = max(parts.values(), key=lambda part: sum(evidence[i].anchored for i in part))
    return [i for i in group if i not in best]


def _agree(evidence: list[Evidence], found: list[int]) -> bool:
    seen = _Profile()
    for i in found:
        profile = _Profile.of(evidence[i])
        if local_conflict(seen, profile) is not None:
            return False
        seen.absorb(profile)
    return True


# ------------------------------------------------------------- fuzzy spellings

_FUZZY_MIN_LENGTH = 6          # consonant skeletons shorter than this are too common
_FUZZY_SCORE = 0.95


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    reach = max(len(a), len(b)) // 2 - 1
    taken_a, taken_b = [False] * len(a), [False] * len(b)
    matches = 0
    for i, char in enumerate(a):
        for j in range(max(0, i - reach), min(len(b), i + reach + 1)):
            if not taken_b[j] and b[j] == char:
                taken_a[i] = taken_b[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    left = [c for c, t in zip(a, taken_a, strict=True) if t]
    right = [c for c, t in zip(b, taken_b, strict=True) if t]
    transpositions = sum(x != y for x, y in zip(left, right, strict=True)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def _unify_spellings(evidence: list["Evidence"]) -> None:
    """Treat nearly identical spellings of a name word as one before linking: same first
    letter, long enough, Jaro-Winkler of at least 0.95 on the consonant skeletons. The merge
    constraints (two Aadhaar numbers, two sexes, ages) still apply afterwards."""
    keys = sorted({k for e in evidence for n in e.summary.names | e.summary.relatives
                   for k in n.keys})
    parent = {k: k for k in keys}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    blocks: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        if len(key.split("|")[0]) >= _FUZZY_MIN_LENGTH:
            blocks[key[:2]].append(key)
    for block in blocks.values():
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                head_a, head_b = a.split("|")[0], b.split("|")[0]     # consonant skeletons
                if abs(len(head_a) - len(head_b)) <= 3 \
                        and jaro_winkler(head_a, head_b) >= _FUZZY_SCORE:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    mapping = {k: find(k) for k in keys if find(k) != k}
    if not mapping:
        return

    def remap(name: Name) -> Name:
        keys_ = frozenset(mapping.get(k, k) for k in name.keys)
        return replace(name, keys=keys_,
                       given=frozenset(mapping.get(k, k) for k in name.given), size=len(keys_))

    for item in evidence:
        item.summary.names = {remap(n) for n in item.summary.names}
        item.summary.relatives = {remap(n) for n in item.summary.relatives}
        item.mentioned = tuple(remap(n) for n in item.mentioned)


def _strong_key(mention: Mention) -> tuple[str, str] | None:
    value = mention.value
    if mention.etype is E.AADHAAR:
        digits = digits_only(value)
        return ("aadhaar", digits) if len(digits) == 12 and len(set(digits)) > 1 else None
    if mention.etype is E.ABHA_NUMBER:
        digits = digits_only(value)
        return ("abha", digits) if len(digits) == 14 and len(set(digits)) > 1 else None
    if mention.etype is E.ABHA_ADDRESS:
        return "email", value.lower()
    if mention.etype is E.PHONE:
        digits = phone_key(value)
        return ("phone", digits) if len(digits) == 10 and len(set(digits)) > 2 else None
    if mention.etype is E.EMAIL:
        return "email", value.lower()
    if mention.etype is E.PAN:
        return "pan", id_key(value)
    if mention.etype in (E.MRN, E.INSURANCE_ID):
        key = id_key(value)
        # A short bare number (e.g. a digit core in a path) is not unique across hospitals;
        # one that a field or label names as a hospital number may be shorter.
        declared = mention.source in ("field", "label")
        if len(key) < 6 or key.isdigit() and len(key) < 8 and not declared:
            return None
        return "hid", key
    return None

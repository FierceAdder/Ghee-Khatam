"""Recognise the same patient across files, languages and hospitals.

Linking is precision first and mirrors the published truth rule: records are joined only
by a strong identifier, or by the same name *plus* a second clue whose combination is
unique. A union-find keeps a summary per cluster and refuses any merge that would put two
Aadhaar numbers, two sexes, impossible ages or two different given names in one patient.
Every iteration is in sorted order, so the result is deterministic.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from wards.detect.demographics import normalize_sex, sexes_in_text
from wards.knowledge import place_key
from wards.lexicon import HONORIFICS_DEVANAGARI, HONORIFICS_LATIN, Lexicon
from wards.model import CLIPPED, NAME_TYPES, Mention, Record, Role
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import digits_only, id_key, phone_key
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


@dataclass(frozen=True)
class Name:
    keys: frozenset[str]
    given: frozenset[str]
    size: int

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
    return Name(keys=frozenset(k for _, k in pairs), given=frozenset(k for _, k in core),
                size=len({k for _, k in pairs}))


def names_compatible(a: Name, b: Name) -> bool:
    return a.keys <= b.keys or b.keys <= a.keys or bool(a.given & b.given)


def record_names(record: Record, lexicon: Lexicon) -> list[tuple[Name, Mention]]:
    """The names in a record, with split first/last fields joined into one name."""
    found: list[tuple[Name, Mention]] = []
    parts: dict[Role, list[tuple[bool, Mention]]] = defaultdict(list)
    for segment, mention in record.mentions():
        if mention.etype not in NAME_TYPES or mention.source == CLIPPED:
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
    for name in sorted(names, key=lambda n: (-n.size, sorted(n.keys))):
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
class Evidence:
    record: Record
    summary: Summary
    anchored: bool          # carries something that identifies a patient
    ambiguous: bool         # describes several people


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
    def __init__(self, lexicon: Lexicon, secret: Secret, phone_hub_groups: int = 3) -> None:
        self.lexicon = lexicon
        self.secret = secret
        self.hub = phone_hub_groups
        self.stats: Counter = Counter()     # decisions, for the log (counts only)

    # ------------------------------------------------------------------ evidence

    def evidence(self, record: Record, order: str, reference_year: int) -> Evidence:
        summary = Summary()
        patient, unknown, relatives = [], [], []
        ages, years = [], []
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
                elif mention.data.year is not None:
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
            else:
                strong = _strong_key(mention)
                if strong:
                    summary.strong.add(strong)

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
        return Evidence(record, summary, anchored, ambiguous)

    @staticmethod
    def _patient_names(patient: list[Name], unknown: list[Name],
                       relatives: list[Name]) -> list[Name]:
        others = [n for n in unknown
                  if not any(n.keys <= r.keys or r.keys <= n.keys for r in relatives)]
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
        """Pseudonym for each evidence index that identifies a patient."""
        self.stats = Counter()
        evidence_order = sorted(range(len(evidence)), key=lambda i: evidence[i].record.key)
        usable = [i for i in evidence_order
                  if evidence[i].anchored and not evidence[i].ambiguous]
        self.stats.update(records=len(evidence),
                          ambiguous=sum(e.ambiguous for e in evidence),
                          unanchored=sum(not e.anchored for e in evidence))
        clusters = Clusters(evidence)
        self._strong_tier(evidence, usable, clusters)
        for name, tier in (("dob", self._name_dob_keys), ("relative", self._name_relative_keys),
                           ("village", self._name_village_keys),
                           ("given", self._given_relative_keys)):
            self._unique_tier(evidence, usable, clusters, tier, name)

        members: dict[int, list[int]] = defaultdict(list)
        for index in usable:
            members[clusters.find(index)].append(index)
        assigned = self._pseudonyms(evidence, clusters, members)
        self.stats.update(linked=len(assigned), patients=len(set(assigned.values())))
        return assigned

    def _strong_tier(self, evidence: list[Evidence], usable: list[int],
                     clusters: Clusters) -> None:
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
                        continue        # a shared desk or family number identifies nobody
                    # records with a name first, so that anonymous ones join afterwards
                    group = sorted(group, key=lambda i: (not evidence[i].summary.names,
                                                         evidence[i].record.key))
                representatives: list[int] = []
                for i in group:
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

    def _unique_tier(self, evidence, usable, clusters: Clusters, keys_of, name: str) -> None:
        index: dict[tuple, list[int]] = defaultdict(list)
        for i in usable:
            for key in sorted(keys_of(clusters.summary[clusters.find(i)], evidence[i].summary)):
                index[key].append(i)
        for key in sorted(index):
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

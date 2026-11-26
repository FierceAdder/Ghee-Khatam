#!/usr/bin/env python3
"""Our own replay of the published re-identification attacks (development only).

    python3 tools/attacker.py --output my_output [--other release_with_other_secret]

Follows ``docs/ATTACKS.md``: record linkage against the registry (A2/A3), pooled profiles
(A4), targeted attack with global-shift recovery (A5), plus direct exposure (A1),
pseudonym inversion (A6) and cross-release linkage (A7). The real attacker's code is not
published, so details here are our reading of the document: treat results as estimates and
compare builds with them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from eval_stage import entity_keys, norm, occurrences, with_payloads
from utility import Release, default_truth, estimate

from wards.dates import DMY, MDY, find_dates
from wards.detect.tokens import Tokens
from wards.knowledge import place_key
from wards.lexicon import Lexicon
from wards.textview import TextView
from wards.veto import Veto

WEIGHTS = {"A1": 0.20, "A2/A3": 0.20, "A4": 0.20, "A5": 0.20, "A6": 0.10, "A7": 0.10}
REFERENCE_YEAR = 2026

_PSEUDONYM = re.compile(r"PSN-[0-9A-Z]{6}")
_REFERENCE_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,8}(?:[\-/]?[A-Z]{2,5})?[\-/]?\d{0,4}[\-/]?"
                           r"\d{5,}(?![A-Za-z0-9])")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[ \-]?|0)?([6-9]\d{2,4}[ \-]?\d{2,3}[ \-]?\d{3,5})(?!\d)")
_MASKED_PHONE = re.compile(r"(?<![A-Za-z0-9])([6-9]\d[Xx*]{4,6}\d{2,4})")
_LAST4 = re.compile(r"(?i)(?:[x*]{4}[ \-]?){2}(\d{4})(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?(\d{4})(?!\d)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
_AGE = (
    re.compile(r"(?i)(?<![a-z])(?:age(?:[ _]?(?:sex|years))?|umar|ageyears|आयु|उम्र)[\"']?\s*[:=]\s*"
               r"[\"']?(\d{1,3}(?:\s*-\s*\d{1,3}|\+)?)"),
    re.compile(r"(?<![\d./\-])(\d{1,3}(?:-\d{1,3}|\+)?)\s*(?:[Yy](?:rs?|ears?)?\.?|वर्ष)?\s*/\s*"
               r"(?:M|F|Male|Female|male|female|पुरुष|महिला)(?![A-Za-z])"),
    re.compile(r"(?<![\d./\-])(\d{1,3}(?:-\d{1,3}|\+)?)\s*(?:yrs?|Yrs|वर्ष)(?=\s*[,/]?\s*"
               r"(?:male|female|महिला|पुरुष))"),
)
_BIRTH = re.compile(r"(?i)(?:dob|birth[ _]?date|janm[^:|]{0,8}|जन्म[^:|]{0,10})[^:\"|]{0,18}"
                    r"[\"']?\s*[:|]\s*[\"']?([0-9][0-9\-/.]{7,9})")
_MALE = re.compile(r"(?i)(?:/\s*m(?![a-z])|\bmale\b|पुरुष|(?<![a-z/])s/o\b|\bsahab\b|\bbhai\b"
                   r"|(?:gender|sex|ling)[\"']?\s*[:|]\s*[\"']?m(?![a-z]))")
_FEMALE = re.compile(r"(?i)(?:/\s*f(?![a-z])|\bfemale\b|महिला|(?<![a-z/])[dw]/o\b|\bbehenji\b|"
                     r"\bdidi\b|(?:gender|sex|ling)[\"']?\s*[:|]\s*[\"']?f(?![a-z]))")
_LABELLED = {
    "district": re.compile(r"(?i)(?:district|distt?\.?|jila|zila|जिला)[\"']?\s*[:|]?\s*[\"']?"
                           r"([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ ]{2,30})"),
    "tehsil": re.compile(r"(?i)(?:tehsil|तहसील)[\"']?\s*[:|]?\s*[\"']?"
                         r"([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ ]{2,30})"),
    "village": re.compile(r"(?i)(?:village(?:/mohalla)?|vill\.|gram|gaon|city|ग्राम)[\"']?\s*[:|]?"
                          r"\s*[\"']?([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ ]{2,30})"),
}
_PIN = re.compile(r"(?i)(?:(?:pin(?:[ _]?code)?|zip|postalcode|पिन(?: कोड)?)[\"']?\s*[:|]?\s*[\"']?"
                  r"|(?:distt?\.?|district|जिला)\s*[A-Za-z\u0900-\u097f ]{2,30}-\s*)"
                  r"([1-9]\d{5}|[1-9]\d{2}\*{3})(?![\d*])")
_PIN_MASKED = re.compile(r"(?<![\d])([1-9]\d{2}\*{3})(?![\d*])")
_HOUSE = re.compile(r"(?i)(?:h\.?\s*no\.?|house|makan(?:\s*no\.?)?|मकान\s*नं\.?)\s*[:#\-]?\s*(\d+)")
_RELATIVE_CUE = re.compile(r"(?i)(?:[sdwc]/o|guardian|relative|father|husband|pita|पिता|पति|"
                           r"attendant|contact)")
_ICD = re.compile(r"(?<![A-Za-z0-9\-])([A-TV-Z]\d{2}(?:\.\d{1,2})?)(?![A-Za-z0-9])")
_ICD_RANGE = re.compile(r"(?<![A-Za-z0-9])([A-Z]\d{2})\s*[-–]\s*([A-Z]\d{2})(?![A-Za-z0-9])")
_HOSPITAL_HINTS = {"sanjeevani": "Sanjeevani Multispeciality Hospital",
                   "jeevanjyoti": "Jeevan Jyoti Charitable Hospital",
                   "jeevan jyoti": "Jeevan Jyoti Charitable Hospital",
                   "arogya": "Arogya Seva Sadan", "dhhardoi": "District Hospital Hardoi",
                   "district hospital hardoi": "District Hospital Hardoi"}


@dataclass(frozen=True)
class Settings:
    rare_cutoff: int = 3
    confident: float = 0.5
    stay_tolerance: int = 0


@dataclass
class Evidence:
    phones: set = field(default_factory=set)
    masked: set = field(default_factory=set)
    emails: set = field(default_factory=set)
    last4: set = field(default_factory=set)
    genders: set = field(default_factory=set)
    ages: set = field(default_factory=set)            # (low, high)
    births: set = field(default_factory=set)
    districts: set = field(default_factory=set)
    tehsils: set = field(default_factory=set)
    villages: set = field(default_factory=set)
    pins: set = field(default_factory=set)
    houses: set = field(default_factory=set)
    names: set = field(default_factory=set)           # phonetic keys
    relatives: set = field(default_factory=set)
    conditions: set = field(default_factory=set)      # codes, or (low, high) ranges
    hospitals: set = field(default_factory=set)
    dates: set = field(default_factory=set)
    stays: set = field(default_factory=set)

    def merge(self, other: "Evidence") -> None:
        for name in self.__dataclass_fields__:
            getattr(self, name).update(getattr(other, name))


@dataclass
class Person:
    rid: str
    name: list
    relative: list
    gender: str
    age: int
    house: str
    village: str
    tehsil: str
    district: str
    pin: str
    phone: str
    email: str
    last4: str


class Attacker:
    def __init__(self, toolkit: Path, truth_path: Path, settings: Settings = Settings()) -> None:
        self.settings = settings
        self.lexicon = Lexicon.load()
        self.veto = Veto(self.lexicon)
        folder = truth_path.parent.parent / "attacker"
        self.people: dict[str, Person] = {}
        with open(folder / "registry.csv", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                keys = lambda value: [self.lexicon.name_key(w) for w in value.split()]  # noqa: E731
                self.people[row["rid"]] = Person(
                    row["rid"], keys(row["name"]), keys(row["relative_name"]), row["gender"],
                    int(row["age"]), row["house_no"], place_key(row["village"]),
                    place_key(row["tehsil"]), place_key(row["district"]), row["pin"],
                    row["phone"], row["email"].casefold(), row["aadhaar_last4"])
        with open(folder / "registry.csv", encoding="utf-8") as handle:
            self.name_words = {self.lexicon.canonical(w) for row in csv.DictReader(handle)
                               for column in ("name", "relative_name")
                               for w in row[column].split()}
        self.places = {kind: {getattr(p, kind) for p in self.people.values()}
                       for kind in ("district", "tehsil", "village")}
        self.facts = [json.loads(line) for line in
                      (folder / "known_facts.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]
        self.truth_path = truth_path
        self.raw = Release(truth_path, None)
        self.conditions = self._vocabulary()
        self.rid_of = {patient: info["rid"]
                       for patient, info in self.raw.truth["patients"].items()}

    # ------------------------------------------------------------------ evidence

    def _vocabulary(self) -> dict[str, str]:
        """Disease names (English and Hindi) to ICD codes, as public knowledge."""
        names: dict[str, str] = {}
        for path in sorted(self.raw.input.rglob("*")):
            if not path.is_file() or path.suffix not in (".json", ".txt"):
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")
            for m in re.finditer(r'"code"\s*:\s*"([A-Z]\d{2}(?:\.\d+)?)"\s*,\s*"display"\s*:\s*'
                                 r'"([^"]+)"', text):
                names[m.group(2)] = m.group(1)
            for m in re.finditer(r"(?:[:;]\s*)([^:;()\n]{3,60}?)\s*\(([A-Z]\d{2}(?:\.\d+)?)\)",
                                 text):
                names[m.group(1).strip()] = m.group(2)
        out = {}
        for name, code in names.items():
            low = norm(name)
            out[low] = code
            short = re.sub(r"^unspecified ", "", low.split(",")[0].split(" (")[0])
            if len(short) > 4:
                out.setdefault(short, code)
        return out

    def extract(self, text: str, order: str, context: str) -> Evidence:
        text = TextView.of(with_payloads(text)).text
        low = text.casefold()
        found = Evidence()
        found.phones = {re.sub(r"\D", "", m.group(1))[-10:] for m in _PHONE.finditer(text)
                        if len(re.sub(r"\D", "", m.group(1))) == 10}
        found.masked = {m.group(1) for m in _MASKED_PHONE.finditer(text)}
        found.emails = {m.group(0).casefold() for m in _EMAIL.finditer(text)}
        found.last4 = {m.group(1) for m in _LAST4.finditer(text)} \
            | {m.group(1) for m in _AADHAAR.finditer(text)}
        male, female = bool(_MALE.search(text)), bool(_FEMALE.search(text))
        if male != female:
            found.genders = {"M" if male else "F"}
        for pattern in _AGE:
            for m in pattern.finditer(text):
                value = m.group(1).replace(" ", "")
                if "-" in value:
                    a, b = map(int, value.split("-"))
                elif value.endswith("+"):
                    a, b = int(value[:-1]), 120
                else:
                    a = b = int(value)
                if a <= b <= 120:
                    found.ages.add((a, b))
        for m in _BIRTH.finditer(text):
            for match in find_dates(m.group(1), typed=True):
                value = match.resolve(order)
                if value:
                    found.births.add(value.year)
        for kind, pattern in _LABELLED.items():
            target = getattr(found, kind + "s")
            for m in pattern.finditer(text):
                words = m.group(1).split()
                for count in range(len(words), 0, -1):
                    key = place_key(" ".join(words[:count]))
                    if key in self.places[kind]:
                        target.add(key)
                        break
        found.pins = {m.group(1) for m in _PIN.finditer(text)} \
            | {m.group(1) for m in _PIN_MASKED.finditer(text)}
        found.houses = {m.group(1) for m in _HOUSE.finditer(text)}
        self._names(text, found)
        found.conditions = {m.group(1) for m in _ICD.finditer(text)}
        found.conditions |= {(m.group(1), m.group(2)) for m in _ICD_RANGE.finditer(text)}
        for name, code in self.conditions.items():
            if name in low:
                found.conditions.add(code)
        hint = low + " " + context.casefold()
        found.hospitals = {full for key, full in _HOSPITAL_HINTS.items() if key in hint}
        for match in find_dates(text, typed=True):
            value = match.resolve(order, REFERENCE_YEAR)
            if value and value.year >= 2020:
                found.dates.add(value)
        ordered = sorted(found.dates)
        found.stays = {(b - a).days for i, a in enumerate(ordered) for b in ordered[i + 1:]
                       if 0 < (b - a).days <= 120}
        return found

    def _names(self, text: str, found: Evidence) -> None:
        tokens = Tokens(text, self.lexicon)
        doctors = self.veto._person_spans(text, tokens)
        cues = [m.end() for m in _RELATIVE_CUE.finditer(text)]
        for index, token in enumerate(tokens.items):
            if not tokens.is_candidate(index) \
                    or any(a <= token.start < b for a, b in doctors):
                continue
            if not token.deva and self.lexicon.canonical(token.text) not in self.name_words:
                continue
            key = self.lexicon.name_key(token.text)
            relative = any(0 <= token.start - c <= 30 for c in cues)
            (found.relatives if relative else found.names).add(key)

    # ------------------------------------------------------------------- linking

    def _filters(self, e: Evidence):
        """The registry filters in the published order, each only when evidence exists."""
        def masked_ok(p, pattern):
            regex = "".join("." if c in "Xx*" else c for c in pattern)
            return bool(p.phone) and bool(re.fullmatch(regex, p.phone))

        tests = (
            (e.phones, lambda p: p.phone in e.phones),
            (e.emails, lambda p: p.email in e.emails),
            (e.last4, lambda p: p.last4 in e.last4),
            (e.masked, lambda p: any(masked_ok(p, m) for m in e.masked)),
            (e.districts, lambda p: p.district in e.districts),
            (e.genders, lambda p: p.gender in e.genders),
            (e.ages, lambda p: any(a - 1 <= p.age <= b + 2 for a, b in e.ages)),
            (e.births, lambda p: any(abs(REFERENCE_YEAR - p.age - y) <= 1 for y in e.births)),
            (e.tehsils, lambda p: p.tehsil in e.tehsils),
            (e.villages, lambda p: p.village in e.villages),
            ({x for x in e.pins if "*" not in x},
             lambda p: p.pin in e.pins),
            ({x[:3] for x in e.pins}, lambda p: p.pin[:3] in {x[:3] for x in e.pins}),
        )
        return [test for evidence, test in tests if evidence]

    def _multiplier(self, e: Evidence, p: Person) -> float:
        weight = 1.0
        if p.name:
            given, surname = p.name[0] in e.names, p.name[-1] in e.names
            if given and surname and len(p.name) > 1:
                weight *= 60
            elif given:
                weight *= 8
            elif surname:
                weight *= 3
        if p.relative and p.relative[0] in e.relatives:
            weight *= 8
        if p.house and p.house in e.houses:
            weight *= 2
        return weight

    def link(self, e: Evidence) -> dict[str, float]:
        """Probability of each registry person for this evidence."""
        candidates = list(self.people.values())
        for test in self._filters(e):
            narrowed = [p for p in candidates if test(p)]
            if narrowed:
                candidates = narrowed
        weights = {p.rid: self._multiplier(e, p) for p in candidates}
        total = sum(weights.values())
        return {rid: w / total for rid, w in weights.items()}

    # ------------------------------------------------------------------- attacks

    def records(self, release: Release) -> list[dict]:
        out = []
        for rel, spec, loc, unit in release.units():
            if loc.startswith("@"):
                continue
            path = release.path(rel, spec)
            if path is None:
                continue
            text = release.text(rel, spec, loc)
            parsed = release.files.load(path, spec)[1]
            if loc.startswith("row:") and isinstance(parsed, list):
                index = int(loc[4:]) + 1
                if index < len(parsed):
                    text = " | ".join(f"{h}: {v}" for h, v in zip(parsed[0], parsed[index],
                                                                   strict=False))
            if not text.strip():
                continue
            order = MDY if spec.get("date_hint") == "mdy" else DMY
            context = rel + " " + release.whole(rel, spec)[:600]
            out.append({"key": (rel, loc), "patient": unit.get("patient"), "text": text,
                        "evidence": self.extract(text, order, context)})
        return out

    def profiles(self, records: list[dict]) -> list[dict]:
        parent = list(range(len(records)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        first: dict[str, int] = {}
        for index, record in enumerate(records):
            keys = _PSEUDONYM.findall(record["text"]) + _REFERENCE_ID.findall(record["text"])
            for key in keys:
                if key in first:
                    parent[find(index)] = find(first[key])
                else:
                    first[key] = index
        groups: dict[int, list[int]] = defaultdict(list)
        for index in range(len(records)):
            groups[find(index)].append(index)
        out = []
        for members in groups.values():
            evidence = Evidence()
            for i in members:
                evidence.merge(records[i]["evidence"])
            owners = Counter(records[i]["patient"] for i in members if records[i]["patient"])
            out.append({"members": members, "evidence": evidence,
                        "patient": owners.most_common(1)[0][0] if owners else None})
        return out

    def linkage(self, records: list[dict], profiles: list[dict]) -> tuple[float, float]:
        """A2/A3 mean probability on the true person, and A4 share re-identified."""
        per_record = []
        identified: dict[str, bool] = defaultdict(bool)
        pooled = {i: self.link(p["evidence"]) for p in profiles for i in p["members"][:1]}
        by_member = {i: pooled[p["members"][0]] for p in profiles for i in p["members"]}
        for index, record in enumerate(records):
            patient = record["patient"]
            if not patient:
                continue
            rid = self.rid_of[patient]
            own = self.link(record["evidence"]).get(rid, 0.0)
            per_record.append(own)
            identified[patient] |= \
                max(own, by_member[index].get(rid, 0.0)) >= self.settings.confident
        record_risk = sum(per_record) / max(len(per_record), 1)
        return record_risk, sum(identified.values()) / max(len(identified), 1)

    def _recover_shift(self, profiles: list[dict]) -> int:
        facts = defaultdict(set)
        for fact in self.facts:
            facts[fact["rid"]].update(_dates(fact["visit_dates"]))
        votes: dict[int, set] = defaultdict(set)
        for profile in profiles:
            probabilities = self.link(profile["evidence"])
            rid, p = max(probabilities.items(), key=lambda kv: (kv[1], kv[0]))
            if p < 0.9 or rid not in facts:
                continue
            for seen in profile["evidence"].dates:
                for known in facts[rid]:
                    votes[(seen - known).days].add(rid)
        agreed = [(len(v), -abs(k), k) for k, v in votes.items() if len(v) >= 2]
        return max(agreed)[2] if agreed else 0

    def targeted(self, profiles: list[dict]) -> tuple[float, int, int, Counter]:
        """A5: mean success against targeted patients, recovered shift and decoy claims."""
        import datetime as dt

        shift = self._recover_shift(profiles)
        patient_of = {rid: patient for patient, rid in self.rid_of.items()}
        matching = Counter()
        for fact in self.facts:
            code = self._code(fact)
            if code:
                matching[code] = sum(_has_condition(p["evidence"], code) for p in profiles)
        successes, decoys = [], 0
        survivors: dict[int, set] = {}
        hits: Counter = Counter()
        for fact in self.facts:
            rid, person = fact["rid"], self.people.get(fact["rid"])
            code = self._code(fact)
            visits = _dates(fact["visit_dates"])
            weights = {}
            for number, profile in enumerate(profiles):
                if number not in survivors:
                    survivors[number] = set(self.link(profile["evidence"]))
                e = profile["evidence"]
                score = 0.0
                if visits & {d - dt.timedelta(days=shift) for d in e.dates}:
                    score += 1
                    hits["date"] += 1
                if code and _has_condition(e, code):
                    score += 8 if matching[code] <= self.settings.rare_cutoff else 1
                    hits["condition"] += 1
                stay = fact.get("stay_days")
                if stay is not None and any(abs(s - stay) <= self.settings.stay_tolerance
                                            for s in e.stays):
                    score += 1
                    hits["stay"] += 1
                if not score or person is None or rid not in survivors[number]:
                    continue
                if fact.get("hospital") in e.hospitals:
                    score *= 2
                weights[number] = score * self._multiplier(e, person)
            total = sum(weights.values())
            patient = patient_of.get(rid)
            if patient is None:
                decoys += bool(total) and max(weights.values()) / total >= self.settings.confident
                continue
            successes.append(sum(w for n, w in weights.items()
                                 if profiles[n]["patient"] == patient) / total if total else 0.0)
        return sum(successes) / max(len(successes), 1), shift, decoys, hits

    def _code(self, fact: dict) -> str | None:
        hint = fact.get("condition_hint")
        return self.conditions.get(norm(hint)) if hint else None

    def exposure(self, release: Release) -> float:
        """A1: share of patients with a surviving direct identifier or full name.

        A key counts when it survives in its own record (or file-level channel). Keys long
        enough to be unmistakable also count anywhere in the release or its side files;
        short digit cores do not, since unrelated serial numbers repeat them.
        """
        if release.output is None:
            return 1.0
        parts = []
        for path in sorted(release.output.rglob("*")):
            parts.append(path.relative_to(release.output).as_posix())
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        everything = norm(with_payloads("\n".join(parts)))
        allowed_anywhere = norm(" ".join(p["text"] for *_, u in release.units()
                                         for p in u.get("preserve") or []))
        exposed, patients = set(), set()
        for rel, spec, loc, unit in release.units():
            patient = unit.get("patient")
            here = None
            for entity in unit["entities"]:
                if not patient or entity["role"] != "patient":
                    continue
                patients.add(patient)
                keys = entity_keys(entity)
                full_name = entity["type"] == "PATIENT_NAME" and entity.get("full")
                if not keys or not (entity.get("critical") or full_name
                                    or entity["type"] == "INSURANCE_ID"):
                    continue
                if here is None:
                    here = norm(with_payloads(release.text(rel, spec, loc)))
                    kept = norm(" ".join(p["text"] for p in unit.get("preserve") or []))
                in_record = all(occurrences(k, here, d) > occurrences(k, kept, d)
                                for k, d in keys)
                long_keys = all(len(k) >= (8 if d else 5) for k, d in keys) \
                    and (full_name or all(d for _, d in keys))
                anywhere = long_keys and all(
                    occurrences(k, everything, d) > occurrences(k, allowed_anywhere, d)
                    for k, d in keys)
                if in_record or anywhere:
                    exposed.add(patient)
        return len(exposed) / max(len(patients), 1)

    def inversion(self, records: list[dict]) -> float:
        """A6: pseudonyms equal to (or a prefix of) a transform of the patient's data."""
        originals: dict[str, set] = defaultdict(set)
        for _, _, _, unit in self.raw.units():
            for entity in unit["entities"]:
                if unit.get("patient") and entity["role"] == "patient":
                    originals[unit["patient"]].add(entity["surface"])
        reversed_, total = set(), set()
        for record in records:
            patient = record["patient"]
            for pseudonym in _PSEUDONYM.findall(record["text"]):
                if not patient:
                    continue
                total.add(patient)
                code = pseudonym[4:].casefold()
                for value in originals[patient]:
                    digests = (hashlib.new(a, value.encode()).hexdigest()
                               for a in ("md5", "sha1", "sha256", "sha512"))
                    if any(d.startswith(code) for d in digests) or code in value.casefold():
                        reversed_.add(patient)
        return len(reversed_) / max(len(total), 1)

    def cross_release(self, records: list[dict], other: list[dict]) -> float:
        """A7: stable pseudonyms or stable date shifts under another secret."""
        second = {r["key"]: r for r in other}
        stable_names, patients = set(), set()
        same = compared = 0
        for record in records:
            twin = second.get(record["key"])
            if twin is None or not record["patient"]:
                continue
            patients.add(record["patient"])
            if set(_PSEUDONYM.findall(record["text"])) & set(_PSEUDONYM.findall(twin["text"])):
                stable_names.add(record["patient"])
            if record["evidence"].dates:
                compared += 1
                same += record["evidence"].dates == twin["evidence"].dates
        return max(len(stable_names) / max(len(patients), 1), same / max(compared, 1))

    def measure(self, release: Release) -> dict:
        records = self.records(release)
        profiles = self.profiles(records)
        record_risk, profile_risk = self.linkage(records, profiles)
        success, shift, decoys, hits = self.targeted(profiles)
        return {"records": records, "A1": self.exposure(release), "A2/A3": record_risk,
                "A4": profile_risk, "A5": success, "A6": self.inversion(records),
                "shift": shift, "decoy_claims": decoys, "hits": dict(hits)}


def _dates(values: list[str]) -> set:
    import datetime as dt

    return {dt.date.fromisoformat(v) for v in values}


def _has_condition(evidence: Evidence, code: str) -> bool:
    """A released code matches if it is the code, its category or a range that covers it."""
    category = code[:3]
    for item in evidence.conditions:
        if isinstance(item, tuple):
            if item[0] <= category <= item[1]:
                return True
        elif item == code or item == category:
            return True
    return False


def attack(truth: Path, output: Path, other: Path | None = None,
           settings: Settings = Settings(), attacker: Attacker | None = None,
           raw: dict | None = None) -> dict:
    """Risks of ``output`` relative to the raw input, and the privacy score."""
    attacker = attacker or Attacker(truth.parents[2], truth, settings)
    raw = raw or attacker.measure(attacker.raw)
    found = attacker.measure(Release(truth, output))
    risks = {"A1": found["A1"]}
    for name in ("A2/A3", "A4", "A5"):
        risks[name] = min(1.0, found[name] / raw[name]) if raw[name] else 0.0
    risks["A6"] = found["A6"]
    if other is not None:
        risks["A7"] = attacker.cross_release(found["records"],
                                             attacker.records(Release(truth, other)))
    risk = sum(WEIGHTS[k] * v for k, v in risks.items()) / sum(WEIGHTS[k] for k in risks)
    utility = estimate(truth, output)["utility"]
    privacy = (1 - risk) * min(1.0, utility / 0.40)
    return {"risks": risks, "absolute": {k: found[k] for k in ("A2/A3", "A4", "A5")},
            "raw": {k: raw[k] for k in ("A2/A3", "A4", "A5")}, "risk": risk,
            "utility": utility, "privacy": privacy,
            "score": 25 * privacy + 20 * utility, "shift": found["shift"],
            "decoy_claims": found["decoy_claims"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", required=True)
    ap.add_argument("--other", help="a release of the same input under another secret")
    ap.add_argument("--toolkit")
    ap.add_argument("--truth")
    ap.add_argument("--rare", type=int, default=3)
    ap.add_argument("--confident", type=float, default=0.5)
    ap.add_argument("--stay-tolerance", type=int, default=0)
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    result = attack(truth, Path(args.output), Path(args.other) if args.other else None,
                    Settings(args.rare, args.confident, args.stay_tolerance))
    print("attack      raw input   release   release / raw")
    for name, value in result["risks"].items():
        raw = result["raw"].get(name)
        shown = result["absolute"].get(name, value)
        print(f"  {name:<8} {'' if raw is None else f'{raw:9.3f}':>9} {shown:9.3f} {value:12.3f}")
    print(f"weighted risk {result['risk']:.3f}   utility {result['utility']:.3f}   "
          f"privacy {result['privacy']:.3f}")
    print(f"privacy x 25 + utility x 20 = {result['score']:.2f}   "
          f"(recovered shift {result['shift']}, decoy claims {result['decoy_claims']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

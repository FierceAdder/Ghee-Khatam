#!/usr/bin/env python3
"""Our own replay of the published re-identification attacks (development only).

    python3 tools/attacker.py --output my_output [--other release_with_other_secret]

Follows ``docs/ATTACKS.md``: record linkage against the registry (A2/A3), pooled profiles
(A4), targeted attack with global-shift recovery (A5), plus direct exposure (A1),
pseudonym inversion (A6) and cross-release linkage (A7). The real attacker's code is not
published, so details here are our reading of the document. ``calibrate_attacker.py``
compares that reading with the organisers' result on the raw practice input; where the
document is silent, the reading that agrees with their numbers is used:

* a record's risk is the larger of its own and its profile's (A2/A3 uses the A4 rule:
  records holding nothing but a name could not otherwise reach the published level);
* only structured patient-identifier fields join records (their number of profiles);
* a condition is recognised by its English disease name; ``--codes`` also reads ICD
  codes and Hindi names, a stronger attacker than the published one appears to be;
* a signal is counted as a hit only on profiles consistent with the targeted person.

Treat results as estimates and compare builds with them.
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

from eval_stage import entity_keys, norm, occurrences, pointer, with_payloads
from utility import Release, default_truth, estimate

from wards.dates import DMY, MDY, find_dates
from wards.knowledge import place_key
from wards.lexicon import Lexicon
from wards.textview import TextView

WEIGHTS = {"A1": 0.20, "A2/A3": 0.20, "A4": 0.20, "A5": 0.20, "A6": 0.10, "A7": 0.10}
REFERENCE_YEAR = 2026

_PSEUDONYM = re.compile(r"PSN-[0-9A-Z]{6}")
# The "shared reference values (patient_ref, uhid, ...)" that join records: structured fields
# whose name is a patient identifier. Letters only, lower case ("PatientID" -> "patientid").
_REFERENCE_FIELD = re.compile(r"uhid|patientref|patientid")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[ \-]?|0)?([6-9]\d{2,4}[ \-]?\d{2,3}[ \-]?\d{3,5})(?!\d)")
_MASKED_PHONE = re.compile(r"(?<![A-Za-z0-9])([6-9]\d[Xx*]{4,6}\d{2,4})")
_LAST4 = re.compile(r"(?i)(?:[x*]{4}[ \-]?){2}(\d{4})(?!\d)")
# Exactly twelve digits: the tail of a longer number (a 14-digit ABHA) is not an Aadhaar.
_AADHAAR = re.compile(r"(?<![\d+])(?<!\d[ \-])\d{4}[ \-]?\d{4}[ \-]?(\d{4})(?![ \-]?\d)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
# The digit groups of a resource identifier are not a phone or Aadhaar number.
_UUID = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![0-9a-f])")
_AGE = (
    re.compile(r"(?i)(?<![a-z])(?:age(?:[ _]?(?:sex|years))?|umar|ageyears|आयु|उम्र)[\"']?\s*[:=]\s*"
               r"[\"']?(\d{1,3}(?:\s*-\s*\d{1,3}|\+)?)"),
    re.compile(r"(?<![\d./\-])(\d{1,3}(?:-\d{1,3}|\+)?)\s*(?:[Yy](?:rs?|ears?)?\.?|वर्ष)?\s*/\s*"
               r"(?:M|F|Male|Female|male|female|पुरुष|महिला)(?![A-Za-z])"),
    re.compile(r"(?<![\d./\-])(\d{1,3}(?:-\d{1,3}|\+)?)\s*(?:yrs?|Yrs|वर्ष)(?=\s*[,/]?\s*"
               r"(?:male|female|महिला|पुरुष))"),
    # "52 वर्ष" on its own, but not a duration ("x 8 वर्ष", "8 वर्ष से", "पिछले 8 वर्ष")
    re.compile(r"(?<![\d./\-])(?<![x×] )(?<![x×])(?<!पिछले )(\d{1,3}(?:-\d{1,3}|\+)?)\s*वर्ष"
               r"(?!\s*(?:से|पहले|पूर्व|तक|पुरान|की अवधि))(?![\u0900-\u097f])"),
)
# Half-open bands such as "[50,60)".
_AGE_INTERVAL = re.compile(r"\[\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)")
_BIRTH = re.compile(r"(?i)(?:dob|birth[ _]?date|janm[^:|]{0,8}|जन्म[^:|]{0,10})[^:\"|]{0,18}"
                    r"[\"']?\s*[:|]\s*[\"']?([0-9][0-9\-/.]{7,9}|(?:19|20)\d\d(?![0-9\-/.]))")
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
_RELATIVE_CUE = re.compile(r"(?i)(?:(?<![a-z])(?:[sdwc]/o|guardian|relative|father|husband|pita|"
                           r"attendant|contact)(?![a-z])|पिता|पति)")
_WORD = re.compile(r"[A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+")
_LINE_BREAK = re.compile(r"[\n|]")
_DOCTOR = re.compile(r"(?:(?<![A-Za-z])(?:Dr|DR|Doctor|DOCTOR)(?![A-Za-z])\.?|डॉक्टर|डॉ\.?|डा\.)"
                     r"[ \t]*(?:[A-Z]\.[ \t]*)*"
                     r"(?:(?:[A-Z][A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+)(?:[ \t]+|(?=\W|$))){1,2}"
                     r"|\(\s*(?:MBBS|MD|MS|DM|DNB|MCh)(?![A-Za-z])[^)\n]*\)"
                     r"|ASHA(?=[ \t]+(?:worker|कार्यकर्ता))")      # a role, as is a doctor's title
_ICD = re.compile(r"(?<![A-Za-z0-9\-])([A-TV-Z]\d{2}(?:\.\d{1,2})?)(?![A-Za-z0-9])")
_ICD_RANGE = re.compile(r"(?<![A-Za-z0-9])([A-Z]\d{2})\s*[-–]\s*([A-Z]\d{2})(?![A-Za-z0-9])")
# A hospital's or laboratory's own name and town are not the patient's address.
_FACILITY = re.compile(r"(?i)(?:district hospital[ \t]+[a-z]+|[a-z ]*(?<![a-z])(?:hospital|sadan|"
                       r"clinic|labs?|pathology|diagnostics)(?![a-z])"
                       r"(?:[ \t]*[,(][ \t]*[a-z ]+\)?)?)")
_HOSPITAL_HINTS = {"sanjeevani": "Sanjeevani Multispeciality Hospital",
                   "jeevanjyoti": "Jeevan Jyoti Charitable Hospital",
                   "jeevan jyoti": "Jeevan Jyoti Charitable Hospital",
                   "arogya": "Arogya Seva Sadan", "dhhardoi": "District Hospital Hardoi",
                   "district hospital hardoi": "District Hospital Hardoi"}


@dataclass(frozen=True)
class Settings:
    rare_cutoff: int = 3
    confident: float = 0.5
    sure: float = 0.9           # a "confident" record (diagnostic only; not published)
    stay_tolerance: int = 0
    condition_codes: bool = False   # also match ICD codes and non-English disease names


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
        self.name_keys = {key for p in self.people.values() for key in p.name + p.relative}
        self.places = {kind: {getattr(p, kind) for p in self.people.values()}
                       for kind in ("district", "tehsil", "village")}
        with open(folder / "registry.csv", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.name_words = {w.casefold() for row in rows for column in ("name", "relative_name")
                           for w in row[column].split()}
        self.spellings = {kind: {"".join(row[kind].split()).casefold(): place_key(row[kind])
                                 for row in rows}
                          for kind in ("district", "tehsil", "village")}
        self._name_cache: dict[str, str] = {}
        self._place_cache: dict[str, tuple[str, str] | None] = {}
        self.facts = [json.loads(line) for line in
                      (folder / "known_facts.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]
        # the hospitals the attacker can name: the practice ones and any a fact mentions
        self.hospital_hints = {**_HOSPITAL_HINTS, **{f["hospital"].casefold(): f["hospital"]
                                                     for f in self.facts if f.get("hospital")}}
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
            # only a closing bracket is an aside: "essential (primary) hypertension" has no
            # shorter name, and "essential" alone names no disease
            short = re.sub(r"^unspecified |\s*\([^()]*\)$", "", low.split(",")[0])
            if len(short) > 4:
                out.setdefault(short, code)
        return out

    def extract(self, text: str, order: str, context: str) -> Evidence:
        text = TextView.of(with_payloads(text)).text
        low = text.casefold()
        found = Evidence()
        numbers = _UUID.sub(" ", text)
        found.phones = {re.sub(r"\D", "", m.group(1))[-10:] for m in _PHONE.finditer(numbers)
                        if len(re.sub(r"\D", "", m.group(1))) == 10}
        found.masked = {m.group(1) for m in _MASKED_PHONE.finditer(numbers)}
        found.emails = {m.group(0).casefold() for m in _EMAIL.finditer(text)}
        found.last4 = {m.group(1) for m in _LAST4.finditer(numbers)} \
            | {m.group(1) for m in _AADHAAR.finditer(numbers)}
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
            if len(m.group(1)) == 4:                # only the year was released
                found.births.add(int(m.group(1)))
            for match in find_dates(m.group(1), typed=True):
                value = match.resolve(order)
                if value:
                    found.births.add(value.year)
        for m in _AGE_INTERVAL.finditer(text):
            a, b = int(m.group(1)), int(m.group(2)) - 1
            if 0 <= a <= b <= 120:
                found.ages.add((a, b))
        for kind, pattern in _LABELLED.items():
            target = getattr(found, kind + "s")
            for m in pattern.finditer(text):
                words = m.group(1).split()
                for count in range(len(words), 0, -1):
                    key = place_key(" ".join(words[:count]))
                    if key in self.places[kind]:
                        target.add(key)
                        break
        self._gazetteer(text, found)
        found.pins = {m.group(1) for m in _PIN.finditer(text)} \
            | {m.group(1) for m in _PIN_MASKED.finditer(text)}
        found.houses = {m.group(1) for m in _HOUSE.finditer(text)}
        self._names(text, found)
        codes = self.settings.condition_codes
        if codes:
            found.conditions = {m.group(1) for m in _ICD.finditer(text)}
            found.conditions |= {(m.group(1), m.group(2)) for m in _ICD_RANGE.finditer(text)}
        for name, code in self.conditions.items():
            if (codes or name.isascii()) and name in low:
                found.conditions.add(code)
        hint = low + " " + context.casefold()
        found.hospitals = {full for key, full in self.hospital_hints.items() if key in hint}
        for match in find_dates(text, typed=True):
            value = match.resolve(order, REFERENCE_YEAR)
            if value:
                found.dates.add(value)
        ordered = sorted(found.dates)
        found.stays = {(b - a).days for i, a in enumerate(ordered) for b in ordered[i + 1:]}
        return found

    def _gazetteer(self, text: str, found: Evidence) -> None:
        """District and village names wherever they stand, in either script.

        A name that is also a district or tehsil is not guessed to be anything narrower
        without its label, and a hospital's name or town is skipped.
        """
        own = [m.span() for m in _FACILITY.finditer(text)]
        words = list(_WORD.finditer(text))
        index = 0
        while index < len(words):
            for count in (3, 2, 1):
                run = words[index:index + count]
                if len(run) < count or any(
                        text[a.end():b.start()] not in (" ", "-")
                        for a, b in zip(run, run[1:], strict=False)):
                    continue
                if not all(w.group()[0].isupper() or w.group()[0] > "\u0900" for w in run):
                    continue
                key = self._place(" ".join(w.group() for w in run))
                if key is None or any(a <= run[0].start() < b for a, b in own):
                    continue
                kind, value = key
                getattr(found, kind + "s").add(value)
                index += count - 1
                break
            index += 1

    def _place(self, name: str) -> tuple[str, str] | None:
        cached = self._place_cache.get(name, False)
        if cached is False:
            spelled = "".join(name.split()).casefold()
            cached = None
            if name[0] > "\u0900":
                key = place_key(name)
                for kind in ("district", "tehsil", "village"):
                    if key in self.places[kind]:
                        if kind != "tehsil":
                            cached = (kind, key)
                        break
            else:                       # Latin: the gazetteer spelling, not a sound-alike
                for kind in ("district", "tehsil", "village"):
                    if spelled in self.spellings[kind]:
                        if kind != "tehsil":
                            cached = (kind, self.spellings[kind][spelled])
                        break
            self._place_cache[name] = cached
        return cached

    def _names(self, text: str, found: Evidence) -> None:
        """Every word that sounds like a registry name, in either script.

        The attacker holds the registry, so a name that is also an everyday word is still
        looked up. Doctors after "Dr." are skipped, and the words just after a relative cue
        count as a relative's name.
        """
        doctors = [m.span() for m in _DOCTOR.finditer(text)]
        cues = [m.end() for m in _RELATIVE_CUE.finditer(text)]
        for m in _WORD.finditer(text):
            word = m.group()
            latin = word[0] < "\u0900"
            if len(word) < 2 or (latin and not word[0].isupper()) \
                    or any(a <= m.start() < b for a, b in doctors):
                continue
            key = self._name_cache.get(word)
            if key is None:
                key = self._name_cache[word] = self._name(word)
            if not key:
                continue
            relative = any(0 <= m.start() - c <= 30
                           and not _LINE_BREAK.search(text, c, m.start()) for c in cues)
            (found.relatives if relative else found.names).add(key)

    def _name(self, word: str) -> str:
        """The registry sound of ``word``, or "".

        A word spelled as in the registry always counts. A sound-alike counts only when it
        is long enough to carry a sound of its own and is not an everyday or medical word
        ("Rx" and "Kin" are no one's name).
        """
        key = self.lexicon.name_key(word)
        if key not in self.name_keys:
            return ""
        if word.casefold() in self.name_words \
                or (len(word) > 3 and not self.lexicon.is_stopword(word)):
            return key
        return ""

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
            # the attacker sees the released file's name, not the one it had in the input
            shown = rel if release.output is None else release.exported[spec["sha256"]]
            context = shown + " " + release.whole(rel, spec)[:600]
            out.append({"key": (rel, loc), "patient": unit.get("patient"), "text": text,
                        "references": _references(loc, parsed),
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
            keys = _PSEUDONYM.findall(record["text"]) + record["references"]
            for key in keys:
                if key in first:
                    parent[find(index)] = find(first[key])
                else:
                    first[key] = index
        groups: dict[int, list[int]] = defaultdict(list)
        for index, record in enumerate(records):
            if record["patient"]:           # the records attacked are the patients' records
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

    def linkage(self, records: list[dict], profiles: list[dict]) -> dict[str, float]:
        """A2/A3 and A4, from each patient record's own evidence and its profile's.

        A record's risk is the larger of the two probabilities on the true person (the A4
        rule); ``linkage_record_only`` leaves the profiles out, for comparison.
        """
        pooled = {}
        for profile in profiles:
            found = self.link(profile["evidence"])
            pooled.update(dict.fromkeys(profile["members"], found))
        alone, risks = [], []
        unique = 0
        identified: dict[str, bool] = defaultdict(bool)
        for index, record in enumerate(records):
            patient = record["patient"]
            if not patient:
                continue
            rid = self.rid_of[patient]
            own, joined = self.link(record["evidence"]), pooled[index]
            risk = max(own.get(rid, 0.0), joined.get(rid, 0.0))
            alone.append(own.get(rid, 0.0))
            risks.append(risk)
            unique += any(len(found) == 1 and rid in found for found in (own, joined))
            identified[patient] |= risk >= self.settings.confident
        count = max(len(risks), 1)
        return {"A2/A3": sum(risks) / count,
                "A4": sum(identified.values()) / max(len(identified), 1),
                "records_attacked": len(risks), "patients_present": len(identified),
                "linkage_record_only": sum(alone) / count,
                "unique_records": unique / count,
                "confident_records": sum(r >= self.settings.sure for r in risks) / count}

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

    def targeted(self, profiles: list[dict], ignore: frozenset = frozenset(),
                 trace: list | None = None) -> tuple[float, int, int, Counter]:
        """A5: mean success against targeted patients, recovered shift, decoy claims, hits.

        ``ignore`` leaves signals ("date", "condition", "stay", "hospital") out, to see
        what each one is worth. ``trace`` receives one entry per targeted patient: which
        signals the fact holds, how many profiles are consistent with the person, and for
        each matching profile its signals, its share of the weight and whether it is right.
        """
        import datetime as dt

        shift = self._recover_shift(profiles)
        patient_of = {rid: patient for patient, rid in self.rid_of.items()}
        matching = Counter()
        for fact in self.facts:
            code = self._code(fact)
            if code:
                matching[code] = sum(_has_condition(p["evidence"], code) for p in profiles)
        successes, decoys = [], 0
        survivors = [set(self.link(p["evidence"])) for p in profiles]
        hits: Counter = Counter()
        for fact in self.facts:
            rid, person = fact["rid"], self.people.get(fact["rid"])
            code = self._code(fact)
            visits = _dates(fact["visit_dates"])
            weights = {}
            matched = {}
            consistent = 0
            for number, profile in enumerate(profiles):
                if person is None or rid not in survivors[number]:
                    continue
                consistent += 1
                e = profile["evidence"]
                signals = []
                if visits & {d - dt.timedelta(days=shift) for d in e.dates}:
                    signals.append("date")
                if code and _has_condition(e, code):
                    signals.append("condition")
                stay = fact.get("stay_days")
                if stay is not None and any(abs(s - stay) <= self.settings.stay_tolerance
                                            for s in e.stays):
                    signals.append("stay")
                signals = [s for s in signals if s not in ignore]
                if not signals:
                    continue
                hits.update(signals)
                score = sum(8 if s == "condition" and matching[code] <= self.settings.rare_cutoff
                            else 1 for s in signals)
                if "hospital" not in ignore and fact.get("hospital") in e.hospitals:
                    score *= 2
                    signals.append("hospital")
                matched[number] = signals
                weights[number] = score * self._multiplier(e, person)
            total = sum(weights.values())
            patient = patient_of.get(rid)
            if patient is None:
                decoys += bool(total) and max(weights.values()) / total >= self.settings.confident
                continue
            successes.append(sum(w for n, w in weights.items()
                                 if profiles[n]["patient"] == patient) / total if total else 0.0)
            if trace is not None:
                holds = [name for name, key in (("date", "visit_dates"), ("stay", "stay_days"),
                                                ("condition", "condition_hint"),
                                                ("hospital", "hospital")) if fact.get(key)]
                trace.append({
                    "holds": holds, "consistent": consistent, "success": successes[-1],
                    "rare": bool(code) and matching[code] <= self.settings.rare_cutoff,
                    "matches": [{"signals": matched[n], "share": w / total,
                                 "right": profiles[n]["patient"] == patient}
                                for n, w in weights.items()]})
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
        seen: dict[tuple[str, bool], int] = {}

        def overall(key: str, digits: bool) -> int:     # the same key recurs in many records
            if (key, digits) not in seen:
                seen[key, digits] = occurrences(key, everything, digits) \
                    - occurrences(key, allowed_anywhere, digits)
            return seen[key, digits]

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
                anywhere = long_keys and all(overall(k, d) > 0 for k, d in keys)
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
        linked = self.linkage(records, profiles)
        trace: list = []
        success, shift, decoys, hits = self.targeted(profiles, trace=trace)
        return {"records": records, "profiles": len(profiles), "A1": self.exposure(release),
                **linked, "A5": success, "targets": len(trace), "A6": self.inversion(records),
                "shift": shift, "decoy_claims": decoys, "hits": dict(hits)}


def _references(loc: str, parsed: object) -> list[str]:
    """Values of the record's patient-identifier fields; placeholders join nothing."""
    pairs: list[tuple[str, object]] = []
    if loc.startswith("row:") and isinstance(parsed, list):
        index = int(loc[4:]) + 1
        if index < len(parsed):
            pairs = list(zip(parsed[0], parsed[index], strict=False))
    elif parsed is not None and not isinstance(parsed, str):
        stack = [("", pointer(parsed, loc) if loc.startswith("/") else parsed)]
        while stack:
            name, node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.items())
            elif isinstance(node, list):
                stack.extend((name, item) for item in node)
            else:
                pairs.append((name, node))
    out = set()
    for name, value in pairs:
        value = "" if value is None else str(value).strip()
        if value and "[" not in value \
                and _REFERENCE_FIELD.search(re.sub(r"[^a-z]", "", str(name).lower())):
            out.add("ref:" + value)
    return sorted(out)


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
    ap.add_argument("--codes", action="store_true",
                    help="also match conditions by ICD code and non-English name")
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    result = attack(truth, Path(args.output), Path(args.other) if args.other else None,
                    Settings(args.rare, args.confident, stay_tolerance=args.stay_tolerance,
                             condition_codes=args.codes))
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

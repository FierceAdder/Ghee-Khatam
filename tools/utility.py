#!/usr/bin/env python3
"""Estimate the clinical-utility score from the published formula (development only).

    python3 tools/utility.py --output my_output [--toolkit ../Secret-Wards-Toolkit]

This follows ``docs/SCORING.md`` section 4 as far as the practice truth allows. It is an
estimate: the official evaluator's probe weights and tolerances are not published, so
compare builds with it rather than quoting it as a score.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from eval_stage import ROOT, Files, norm, pointer, with_payloads

from wards.dates import DMY, MDY, find_dates

WEIGHTS = {"clinical_probes": 0.35, "temporal": 0.20, "generalization": 0.10,
           "statistics": 0.15, "token_retention": 0.10, "record_retention": 0.10}
_TOKEN = re.compile(r"[\w\u0900-\u097f]+")
_TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\d(?:t[\d:.+\-]+z?)?")


class Release:
    """One output folder (or the raw input) read through the truth file."""

    def __init__(self, truth_path: Path, output: Path | None) -> None:
        self.raw: Release | None = None        # the input, for comparisons
        self.truth = json.loads(truth_path.read_text(encoding="utf-8"))
        self.input = truth_path.parent.parent / "input"
        self.output = output
        self.files = Files()
        self.exported: dict[str, str] = {}
        self.claims: dict[tuple[str, str], str | None] = {}
        if output is not None:
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.exported = {e["input_sha256"]: e["output"] for e in report["files"]
                             if e.get("output")}
            for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.claims[row["file"], row["loc"]] = row["patient"]

    def path(self, rel: str, spec: dict) -> Path | None:
        if self.output is None:
            return self.input / rel
        output = self.exported.get(spec["sha256"])
        return None if output is None else self.output / output

    def source_text(self, rel: str, spec: dict, loc: str) -> str:
        return self.files.unit(self.input / rel, spec, loc, rel)

    def text(self, rel: str, spec: dict, loc: str) -> str:
        path = self.path(rel, spec)
        if path is None:
            return ""
        shown = rel if self.output is None else \
            self.exported[spec["sha256"]].replace("sanitized/", "", 1)
        return self.files.unit(path, spec, loc, shown)

    def whole(self, rel: str, spec: dict) -> str:
        path = self.path(rel, spec)
        return "" if path is None else self.files.load(path, spec)[0]

    def claim(self, spec: dict, loc: str) -> str | None:
        output = self.exported.get(spec["sha256"])
        if output is None:
            return None
        return self.claims.get((output, loc), self.claims.get((output, "doc")))

    def units(self):
        for rel, spec in sorted(self.truth["files"].items()):
            for loc, unit in spec["units"].items():
                yield rel, spec, loc, unit


def date_offsets(release: Release, rel: str, spec: dict, loc: str, unit: dict):
    """``(truth entry, days moved or None)`` for every clinical date of one record."""
    entries = unit.get("dates") or []
    if not entries:
        return []
    births = [dt.date.fromisoformat(e["key"]) for e in unit["entities"]
              if e.get("match") == "dob"]
    before = find_dates(release.source_text(rel, spec, loc), typed=True)
    after = find_dates(release.text(rel, spec, loc), typed=True)
    if len(before) != len(after):
        before = [m for m in before
                  if not any(m.resolve(o) == b for b in births for o in (DMY, MDY))]
    out, used = [], set()
    for entry in entries:
        true = dt.date.fromisoformat(entry["true"])
        order = MDY if entry.get("hint") == "mdy" else DMY
        moved = None
        if len(before) == len(after):
            for index, match in enumerate(before):
                if index not in used and match.resolve(order, true.year) == true:
                    used.add(index)
                    value = after[index].resolve(order, true.year)
                    if value is not None:
                        moved = (value - true).days
                        if after[index].year is None:
                            moved = (moved + 182) % 365 - 182
                    break
        out.append((entry, moved))
    return out


# ----------------------------------------------------------------------- parts


def clinical_probes(release: Release) -> float:
    total = score = 0.0
    for rel, spec, loc, unit in release.units():
        probes = unit.get("preserve") or []
        if not probes:
            continue
        here = norm(with_payloads(release.text(rel, spec, loc)))
        anywhere = None
        for probe in probes:
            needle = norm(probe["text"])
            total += 1
            if needle in here:
                score += 1.0
            else:
                if anywhere is None:
                    anywhere = norm(with_payloads(release.whole(rel, spec)))
                score += 0.8 if needle in anywhere else 0.0
    return score / total if total else 1.0


def temporal(release: Release) -> float:
    events: dict[str, list] = defaultdict(list)
    stays_kept = stays = 0.0
    for rel, spec, loc, unit in release.units():
        if not unit.get("patient"):
            continue
        offsets = date_offsets(release, rel, spec, loc, unit)
        events[unit["patient"]] += [moved for _, moved in offsets]
        named = {entry.get("event"): moved for entry, moved in offsets}
        if "admission" in named and "discharge" in named:
            stays += 1
            a, b = named["admission"], named["discharge"]
            if a is not None and b is not None:
                stays_kept += {0: 1.0, 1: 0.5}.get(abs(a - b), 0.0)
    score = total = 0.0
    for moved in events.values():
        counts = Counter(moved)
        n = len(moved)
        total += n * (n - 1) / 2
        for (a, x), (b, y) in itertools.combinations_with_replacement(counts.items(), 2):
            pairs = x * (x - 1) / 2 if a == b else x * y
            if a is None or b is None:
                continue
            score += pairs * {0: 1.0, 1: 0.5}.get(abs(a - b), 0.0)
    intervals = score / total if total else 1.0
    stay = stays_kept / stays if stays else 1.0
    return 0.6 * intervals + 0.4 * stay


def _field_value(release: Release, rel: str, spec: dict, loc: str, path: str) -> str | None:
    target = release.path(rel, spec)
    if target is None:
        return None
    parsed = release.files.load(target, spec)[1]
    if path.startswith("col:") and isinstance(parsed, list):
        header, index = parsed[0], int(loc[4:]) + 1
        source = release.files.load(release.input / rel, spec)[1][0]
        name = path[4:]
        if name not in source or index >= len(parsed):
            return None
        position = source.index(name)
        row = parsed[index]
        return row[position] if position < len(row) and position < len(header) else None
    if parsed is None:
        return None
    full = path if loc == "doc" or path.startswith(loc) else loc + path
    value = pointer(parsed, full)
    return None if value is None else str(value)


def _age_score(value: str, true: int) -> float:
    band = re.search(r"(\d+)\s*-\s*(\d+)", value)
    if band:
        low, high = int(band.group(1)), int(band.group(2))
        if not low <= true <= high:
            return 0.0
        width = high - low + 1
        return 0.9 if width <= 5 else 0.8 if width <= 10 else 0.6 if width <= 20 else 0.3
    top = re.search(r"(\d+)\+", value)
    if top:
        return 0.9 if true >= int(top.group(1)) else 0.0
    exact = re.search(r"\d+", value)
    return 1.0 if exact and int(exact.group()) == true else 0.0


def generalization(release: Release) -> float:
    total = score = 0.0
    for rel, spec, loc, unit in release.units():
        for item in unit.get("fields") or []:
            path, kind = item.get("path"), item["kind"]
            if not path:
                continue
            value = _field_value(release, rel, spec, loc, path)
            total += 1
            if value is None:
                continue
            true = item["true"]
            if kind == "age":
                score += _age_score(value, int(true))
            elif kind == "dob":
                found = find_dates(value, typed=True)
                dates = [m.resolve(o) for m in found for o in (DMY, MDY)]
                birth = dt.date.fromisoformat(str(true))
                if birth in dates:
                    score += 1.0
                elif any(d and abs((d - birth).days) <= 366 for d in dates):
                    score += 0.9
                elif value.strip() == str(birth.year):
                    score += 0.8
            elif kind == "pin":
                score += 1.0 if str(true) in value else \
                    0.7 if value.startswith(str(true)[:3]) else 0.0
            elif kind == "village":
                original = _field_value(release.raw, rel, spec, loc, path)
                score += 1.0 if value == original else 0.7 if value and "[" not in value else 0.0
            else:
                score += 1.0 if value.strip() and "[" not in value else 0.0
    return score / total if total else 1.0


def _tvd(a: Counter, b: Counter) -> float:
    total_a, total_b = sum(a.values()), sum(b.values())
    if not total_a:
        return 0.0
    if not total_b:
        return 1.0
    return 0.5 * sum(abs(a[k] / total_a - b[k] / total_b) for k in set(a) | set(b))


def statistics(release: Release) -> float:
    true_codes, kept_codes = Counter(), Counter()
    true_stays, kept_stays = Counter(), Counter()
    for rel, spec, loc, unit in release.units():
        codes = [p["text"] for p in unit.get("preserve") or [] if p["kind"] == "diagnosis_code"]
        if codes:
            text = release.text(rel, spec, loc)
            for code in codes:
                true_codes[code] += 1
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![\d.])", text):
                    kept_codes[code] += 1
        offsets = date_offsets(release, rel, spec, loc, unit)
        named = {e.get("event"): (e, moved) for e, moved in offsets}
        if "admission" in named and "discharge" in named:
            (a, x), (b, y) = named["admission"], named["discharge"]
            length = (dt.date.fromisoformat(b["true"]) - dt.date.fromisoformat(a["true"])).days
            true_stays[length] += 1
            if x is not None and y is not None:
                kept_stays[length + y - x] += 1
    parts = []
    for true, kept in ((true_codes, kept_codes), (true_stays, kept_stays)):
        if true:
            coverage = min(1.0, sum(kept.values()) / sum(true.values()))
            parts.append((1 - _tvd(true, kept)) * coverage)
    parts.append(1.0)             # lab values are never altered, so medians are unchanged
    return sum(parts) / len(parts)


def token_retention(release: Release) -> float:
    kept = total = 0
    for rel, spec, loc, unit in release.units():
        if loc.startswith("@"):
            continue
        source = norm(release.source_text(rel, spec, loc))
        for entity in unit["entities"]:
            source = source.replace(norm(entity["surface"]), " ")
        # dates and ages are excluded from this measure, as in the published formula
        source = _TIMESTAMP.sub(" ", source)
        before = Counter(t for t in _TOKEN.findall(source) if not t.isdigit())
        after = Counter(_TOKEN.findall(norm(release.text(rel, spec, loc))))
        total += sum(before.values())
        kept += sum(min(n, after[t]) for t, n in before.items())
    return kept / total if total else 1.0


def record_retention(release: Release) -> float:
    found = total = 0
    for rel, spec, loc, _ in release.units():
        if loc.startswith("@") or spec.get("expect") == "either":
            continue
        total += 1
        found += bool(release.text(rel, spec, loc).strip())
    return found / total if total else 1.0


def estimate(truth_path: Path, output: Path) -> dict[str, float]:
    release = Release(truth_path, output)
    release.raw = Release(truth_path, None)
    parts = {
        "clinical_probes": clinical_probes(release),
        "temporal": temporal(release),
        "generalization": generalization(release),
        "statistics": statistics(release),
        "token_retention": token_retention(release),
        "record_retention": record_retention(release),
    }
    parts["utility"] = sum(WEIGHTS[k] * v for k, v in parts.items())
    return parts


def default_truth(toolkit: str | None) -> Path:
    base = Path(toolkit) if toolkit else ROOT.parent / "Secret-Wards-Toolkit"
    return base / "dev" / "truth" / "truth.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", required=True)
    ap.add_argument("--toolkit")
    ap.add_argument("--truth")
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    for name, value in estimate(truth, Path(args.output)).items():
        print(f"  {name:<18} {value:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

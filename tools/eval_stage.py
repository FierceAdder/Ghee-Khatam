#!/usr/bin/env python3
"""Per-stage measurements against a truth file (development only, never part of a run).

    python3 tools/eval_stage.py --output my_output [--toolkit ../Secret-Wards-Toolkit] [--show 20]

Goes further than the toolkit's ``check_dev.py``:

* leaks are counted per record on identifying name tokens and digit identifiers, which is
  closer to how the official scorer counts them;
* every true patient's dates must move by one single offset (stay lengths stay exact);
* age bands, PIN prefixes and villages must be truthful generalisations;
* linking is reported per trap, and every pseudonym claim must appear in its record.

Standard library only, plus the ``wards`` package for date parsing.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import itertools
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wards.dates import DMY, MDY, find_dates  # noqa: E402

_DIGIT_MATCH = {"digits", "digits_exact"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return " ".join("".join(c for c in text if c not in "​‌‍﻿")
                    .casefold().split())


def with_payloads(text: str) -> str:
    import base64

    extra = []
    for token in re.findall(r"[A-Za-z0-9+/]{24,}={0,2}", text):
        try:
            extra.append(base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8"))
        except ValueError:
            pass
    return "\n".join([text, *extra])


class Files:
    """Read records out of a directory of exports by truth locator."""

    def __init__(self) -> None:
        self.cache: dict[Path, tuple[str, object]] = {}

    def load(self, path: Path, spec: dict) -> tuple[str, object]:
        if path not in self.cache:
            raw = path.read_bytes()
            encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
            parsed: object = None
            if spec.get("format") == "csv":
                parsed = list(csv.reader(io.StringIO(text, newline=""),
                                         delimiter=spec.get("delimiter") or ","))
            elif text.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(text)
                except (ValueError, RecursionError):
                    parsed = None
            self.cache[path] = (text, parsed)
        return self.cache[path]

    def unit(self, path: Path, spec: dict, loc: str, rel: str) -> str:
        text, parsed = self.load(path, spec)
        if loc == "@filename":
            return rel
        if loc == "@header" and parsed:
            return " | ".join(parsed[0])
        if loc.startswith("row:") and isinstance(parsed, list):
            index = int(loc[4:]) + 1
            return " | ".join(parsed[index]) if index < len(parsed) else ""
        if loc.startswith("/") and parsed is not None:
            node = pointer(parsed, loc)
            return "" if node is None else json.dumps(node, ensure_ascii=False)
        return text


def pointer(document: object, path: str) -> object:
    node = document
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return node


def occurrences(key: str, text: str, digits: bool) -> int:
    if digits:
        pattern = r"[ \-./()]*".join(map(re.escape, key))
        return len(re.findall(pattern, text))
    if re.fullmatch(r"[\wऀ-ॿ]+", key):
        return len(re.findall(rf"(?<![a-z0-9ऀ-ॿ]){re.escape(key)}"
                              rf"(?![a-z0-9ऀ-ॿ])", text))
    return text.count(key)


def entity_keys(entity: dict) -> list[tuple[str, bool]]:
    kind = entity.get("match")
    if kind == "name":
        return [(norm(t), False) for t in entity.get("tokens") or []]
    if kind in _DIGIT_MATCH:
        return [(entity["key"], True)]
    if kind == "dob":
        return []                      # shifted dates of birth are judged separately
    return [(norm(entity.get("key") or entity["surface"]), False)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", required=True)
    ap.add_argument("--toolkit", default=os.environ.get(
        "TOOLKIT_DIR", str(ROOT.parent / "Secret-Wards-Toolkit")))
    ap.add_argument("--truth", help="truth.json (default: <toolkit>/dev/truth/truth.json)")
    ap.add_argument("--show", type=int, default=10, help="examples to list per section")
    args = ap.parse_args()
    truth_path = Path(args.truth) if args.truth \
        else Path(args.toolkit) / "dev" / "truth" / "truth.json"
    if not truth_path.is_file():
        print("truth file not found; pass --toolkit or --truth", file=sys.stderr)
        return 2
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    input_dir = truth_path.parent.parent / "input"
    out = Path(args.output)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    exported = {e["input_sha256"]: e.get("output") for e in report["files"]}
    claims: dict[str, dict[str, str | None]] = defaultdict(dict)
    for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        claims[row["file"]][row["loc"]] = row["patient"]

    source, target = Files(), Files()
    totals, leaked = Counter(), Counter()
    script_total, script_leaked = Counter(), Counter()
    keep_total, keep_score = Counter(), Counter()
    leaks, losses, claim_errors, band_errors = [], [], [], []
    units, offsets = [], defaultdict(list)
    birth_records, birth_revealed = 0, []
    fields_ok, fields_total = Counter(), Counter()

    for rel, spec in sorted(truth["files"].items()):
        output = exported.get(spec["sha256"])
        if not output:
            continue
        path, original = out / output, input_dir / rel
        for loc, unit in spec["units"].items():
            relative = output.replace("sanitized/", "", 1)
            before = norm(with_payloads(source.unit(original, spec, loc, rel)))
            target_text = norm(with_payloads(target.unit(path, spec, loc, relative)))
            probes = [norm(p["text"]) for p in unit.get("preserve") or []]

            for entity in unit["entities"]:
                keys = entity_keys(entity)
                if not keys:
                    continue
                kind = entity["type"]
                hits = 0
                for key, digits in keys:
                    allowed = sum(occurrences(key, p, digits) for p in set(probes)
                                  if p in before)
                    if occurrences(key, target_text, digits) > allowed:
                        hits += 1
                totals[kind] += 1
                script_total[entity["script"]] += 1
                if hits:
                    leaked[kind] += hits / len(keys)
                    script_leaked[entity["script"]] += hits / len(keys)
                    leaks.append((kind, rel, loc, entity["surface"]))

            whole = norm(with_payloads(target.load(path, spec)[0]))
            for probe in unit.get("preserve") or []:
                needle = norm(probe["text"])
                keep_total[probe["kind"]] += 1
                if needle in target_text:
                    keep_score[probe["kind"]] += 1.0
                elif needle in whole:
                    keep_score[probe["kind"]] += 0.8
                else:
                    losses.append((probe["kind"], rel, loc, probe["text"]))

            if any(e.get("match") == "dob" for e in unit["entities"]):
                birth_records += 1
                if _reveals_birth_year(unit, target, path, spec, loc):
                    birth_revealed.append((rel, loc))
            claim = claims.get(output, {}).get(loc, claims.get(output, {}).get("doc"))
            own = claims.get(output, {}).get(loc)
            if own and own not in target.unit(path, spec, loc, relative):
                claim_errors.append((rel, loc, own))
            if unit.get("patient") and not loc.startswith("@"):
                units.append((unit["patient"], claim, unit.get("link"), rel, loc))
                _collect_dates(unit, spec, source, target, original, path, loc, offsets)
                _check_fields(unit, target, path, spec, loc, fields_ok, fields_total,
                              band_errors)

    show = args.show
    print("== detection (identifying keys still visible) ==")
    for kind in sorted(totals, key=lambda k: (-leaked[k] / totals[k], k)):
        print(f"  {kind:<16} {leaked[kind]:7.1f} / {totals[kind]:<5} "
              f"{100 * leaked[kind] / totals[kind]:5.1f}%")
    for script in sorted(script_total):
        print(f"  script {script:<9} {100 * script_leaked[script] / script_total[script]:5.2f}%")
    for item in leaks[:show]:
        print("    leak", item)

    print("\n== medical and context values kept ==")
    total = sum(keep_total.values())
    print(f"  overall {100 * sum(keep_score.values()) / max(total, 1):.2f}% of {total}")
    for kind in sorted(keep_total):
        share = keep_score[kind] / keep_total[kind]
        if share < 1:
            print(f"  {kind:<16} {100 * share:5.1f}%")
    for item in losses[:show]:
        print("    lost", item)

    print("\n== linking ==")
    _linking(units, truth.get("traps") or {}, show)
    print(f"  pseudonym claims missing from their record: {len(claim_errors)}")
    for item in claim_errors[:show]:
        print("    claim", item)

    print("\n== dates (one offset per true patient) ==")
    consistent = pairs = exact = 0
    broken = []
    for patient, values in sorted(offsets.items()):
        spread = Counter(v for v, _ in values)
        consistent += len(spread) == 1
        n = sum(spread.values())
        pairs += n * (n - 1) // 2
        exact += sum(c * (c - 1) // 2 for c in spread.values())
        if len(spread) > 1:
            common = spread.most_common(1)[0][0]
            broken.append((patient, common, [s for v, s in values if v != common][:3]))
    print(f"  patients with a single offset: {consistent}/{len(offsets)}")
    print(f"  event-pair intervals exact:    {100 * exact / max(pairs, 1):.2f}% of {pairs}")
    zero = sum(1 for values in offsets.values() for v, _ in values if v == 0)
    print(f"  dates left unshifted:          {zero}")
    for item in broken[:show]:
        print("    offsets", item)

    print(f"  records revealing a birth year: {len(birth_revealed)}/{birth_records}")
    for item in birth_revealed[:show]:
        print("    birth year", item)

    print("\n== generalised fields (truthful and informative) ==")
    for kind in sorted(fields_total):
        print(f"  {kind:<10} {fields_ok[kind]}/{fields_total[kind]}")
    for item in band_errors[:show]:
        print("    field", item)
    return 0


def _collect_dates(unit, spec, source, target, original, path, loc, offsets) -> None:
    """Offsets between each true clinical date and the date written in its place."""
    truth_dates = unit.get("dates") or []
    if not truth_dates:
        return
    before_text = source.unit(original, spec, loc, "")
    after_text = target.unit(path, spec, loc, "")
    before, after = find_dates(before_text, typed=True), find_dates(after_text, typed=True)
    if len(before) != len(after):
        births = _birth_dates(unit)
        before = [m for m in before
                  if not any(m.resolve(order) == b for b in births for order in (DMY, MDY))]
    if len(before) != len(after):
        return
    for entry in truth_dates:
        true = dt.date.fromisoformat(entry["true"])
        order = MDY if entry.get("hint") == "mdy" else DMY
        for index, match in enumerate(before):
            if match.resolve(order, true.year) == true:
                moved = after[index].resolve(order, true.year)
                if moved is not None:
                    delta = (moved - true).days
                    if after[index].year is None:      # year-less: compare modulo a year
                        delta = (delta + 182) % 365 - 182
                    offsets[unit["patient"]].append((delta, (spec["path"], loc, entry["true"])))
                break


def _birth_dates(unit) -> list:
    return [dt.date.fromisoformat(e["key"]) for e in unit["entities"] if e.get("match") == "dob"]


def _reveals_birth_year(unit, target, path, spec, loc) -> bool:
    """Does any date left in the record fall within a year of the true birth date?"""
    births = [b for b in _birth_dates(unit) if b.year < 2024]   # newborn visits coincide
    if not births:
        return False
    found = find_dates(target.unit(path, spec, loc, ""), typed=True)
    return any(value is not None and abs((value - birth).days) <= 366
               for m in found for order in (DMY, MDY)
               for value in [m.resolve(order)] for birth in births)


def _check_fields(unit, target, path, spec, loc, ok, total, errors) -> None:
    for item in unit.get("fields") or []:
        kind, field_path = item["kind"], item.get("path")
        if kind not in ("age", "pin", "district") or not field_path:
            continue
        text, parsed = target.load(path, spec)
        value = None
        if field_path.startswith("col:") and isinstance(parsed, list):
            index = int(loc[4:]) + 1
            source_header = field_path[4:]
            header = parsed[0]
            position = header.index(source_header) if source_header in header else None
            if position is not None and index < len(parsed) and position < len(parsed[index]):
                value = parsed[index][position]
        elif parsed is not None:
            full = field_path if loc == "doc" or field_path.startswith(loc) \
                else loc + field_path
            value = pointer(parsed, full)
        if value is None:
            continue
        value = str(value)
        total[kind] += 1
        true = item["true"]
        if kind == "age":
            band = re.search(r"(\d+)\s*-\s*(\d+)|(\d+)\+|(\d+)", value)
            if band and band.group(1):
                good = int(band.group(1)) <= int(true) <= int(band.group(2))
            elif band and band.group(3):
                good = int(true) >= int(band.group(3))
            else:
                good = bool(band) and int(band.group(4)) == int(true)
        elif kind == "pin":
            good = value.startswith(str(true)[:3]) and value != str(true)
        else:
            good = bool(value.strip())
        ok[kind] += good
        if not good:
            errors.append((kind, spec["path"], loc, value, true))


def _linking(units, traps, show) -> None:
    by_claim = defaultdict(list)
    for index, (_, claim, *_rest) in enumerate(units):
        if claim:
            by_claim[claim].append(index)
    true_positive = false_positive = 0
    merges = []
    for claim, members in sorted(by_claim.items()):
        for i, j in itertools.combinations(members, 2):
            if units[i][0] == units[j][0]:
                true_positive += 1
            else:
                false_positive += 1
        if len({units[i][0] for i in members}) > 1:
            merges.append((claim, sorted({units[i][0] for i in members})))

    def recall(patients=None):
        found = wanted = 0
        groups = defaultdict(list)
        for patient, claim, link, *_ in units:
            if link != "ambiguous" and (patients is None or patient in patients):
                groups[patient].append(claim)
        for claimed in groups.values():
            wanted += len(claimed) * (len(claimed) - 1) // 2
            found += sum(c * (c - 1) // 2 for k, c in Counter(claimed).items() if k)
        return found / wanted if wanted else 1.0

    precision = true_positive / max(true_positive + false_positive, 1)
    print(f"  pairwise precision {precision:.4f}   recall {recall():.4f}")
    for trap in ("language", "fragmented"):
        if traps.get(trap):
            print(f"  recall on {trap:<10} patients: {recall(set(traps[trap])):.4f}")
    pairs = traps.get("homonym_pairs") or []
    separated = 0
    for first, second in pairs:
        a = {c for p, c, *_ in units if p == first and c}
        b = {c for p, c, *_ in units if p == second and c}
        separated += not a & b
    print(f"  homonym pairs kept apart: {separated}/{len(pairs)}")
    for item in merges[:show]:
        print("    false merge", item)
    split = defaultdict(Counter)
    for patient, claim, link, *_ in units:
        if link == "required":
            split[patient][claim] += 1
    fragmented = [(p, dict(c)) for p, c in sorted(split.items()) if len(c) > 1]
    print(f"  patients split over several pseudonyms or null: {len(fragmented)}")
    for patient, counts in fragmented[:show]:
        main_claim = max(counts, key=counts.get)
        examples = [(file, where, claim) for p, claim, link, file, where in units
                    if p == patient and link == "required" and claim != main_claim][:3]
        print("    split", patient, examples)


if __name__ == "__main__":
    raise SystemExit(main())

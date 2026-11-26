#!/usr/bin/env python3
"""Generate a test dataset with an exact answer key (development only).

    python3 tools/generator/build.py --out /tmp/gen_train --split train --seed 1
    python3 tools/generator/build.py --out /tmp/gen_test  --split test  --seed 1
    python3 main.py --input /tmp/gen_test/input --output /tmp/gen_test_out
    python3 ../Secret-Wards-Toolkit/check_dev.py --output /tmp/gen_test_out \\
        --truth /tmp/gen_test/truth/truth.json

``train`` uses layouts and wording like the practice data plus the training ingredients in
vocab.py; ``test`` uses only held-out ingredients and layouts the practice data does not
contain. Never look at test failures to decide what to fix; fix families of cases found
on the training side, then measure the test side.

Writes ``input/``, ``truth/truth.json`` (the toolkit's format, readable by check_dev.py)
and ``truth/report.txt`` (coverage, novelty and realism).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from generator import layouts  # noqa: E402
from generator.annotate import Doc, norm  # noqa: E402
from generator.world import World  # noqa: E402
from utility import ROOT, default_truth  # noqa: E402

GENERATOR_VERSION = "1.0"
_LABEL_LINE = re.compile(r"(?m)^[ \t]*([^:\n]{2,30}?)[ \t]*(?::|[ \t]-[ \t])")


def build(split: str, seed: int, patients: int = 40) -> tuple[World, list[Doc]]:
    rng = random.Random(f"{split}:{seed}")
    world = World(rng, split, patients)
    ctx = layouts.Ctx(world, rng)
    plan = layouts.TRAIN if split == "train" else layouts.TEST
    docs: list[Doc] = []
    counter = iter(range(1, 100000))
    by_hospital = {h.code: [p for p in world.patients if h.code in p.ids]
                   for h in world.hospitals}
    for hospital in world.hospitals:
        members = by_hospital[hospital.code]
        if members:
            docs.append(plan["register"](ctx, hospital, members))
    for patient in world.patients:
        suitable = [f for f in plan["single"] if layouts.suits(f, patient)]
        for layout in rng.sample(suitable, rng.choice((2, 3))):
            docs.append(layout(ctx, patient, next(counter)))
    if split == "train":
        for hospital in world.hospitals[:2]:
            if by_hospital[hospital.code]:
                docs.append(plan["lab_table"](ctx, hospital, by_hospital[hospital.code]))
        for hospital in world.hospitals:
            members = by_hospital[hospital.code]
            for start in range(0, len(members), 6):
                docs.append(plan["json_export"](ctx, hospital, members[start:start + 6],
                                                next(counter)))
    else:
        shuffled = rng.sample(world.patients, len(world.patients))
        for start in range(0, len(shuffled), 3):
            docs.append(plan["multi"][0](ctx, shuffled[start:start + 3], next(counter)))
        for start in range(0, len(shuffled), 6):
            docs.append(plan["multi"][1](ctx, shuffled[start:start + 6], next(counter)))
        for hospital in world.hospitals[:2]:
            if by_hospital[hospital.code]:
                docs.append(plan["lists"][0](ctx, by_hospital[hospital.code], hospital))
                docs.append(plan["lists"][1](ctx, by_hospital[hospital.code], hospital))
        docs.append(plan["roster"](ctx, next(counter)))
    docs.extend(layouts.canary_docs(ctx))
    _encodings(docs, rng)
    _unique_paths(docs)
    return world, docs


def _encodings(docs: list[Doc], rng: random.Random) -> None:
    for doc in docs:
        if doc.fmt == "csv":
            choice = rng.random()
            if choice < 0.2:
                doc.encoding = "utf-16"
            elif choice < 0.4:
                doc.encoding = "utf-8-sig"
            elif choice < 0.5 and doc.text.isascii():
                doc.encoding = "cp1252"


def _unique_paths(docs: list[Doc]) -> None:
    seen: Counter = Counter()
    for doc in docs:
        seen[doc.rel] += 1
        if seen[doc.rel] > 1:
            stem, dot, suffix = doc.rel.rpartition(".")
            doc.rel = f"{stem}_{seen[doc.rel]}{dot}{suffix}"


def write(world: World, docs: list[Doc], destination: Path, split: str, seed: int) -> dict:
    shutil.rmtree(destination, ignore_errors=True)
    files = {}
    for doc in docs:
        raw = doc.text.encode(doc.encoding)
        target = destination / "input" / doc.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files[doc.rel] = {
            "path": doc.rel, "format": doc.fmt, "encoding": doc.encoding,
            "delimiter": doc.delimiter, "expect": "sanitize", "category": doc.category,
            "layout": doc.layout, "clean": False,
            "date_hint": "dmy", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            "generalizable": [],
            "units": {loc: {"loc": loc, "patient": u.patient, "cid": f"{u.patient}#{loc}",
                            "link": u.link, "tags": [], "entities": u.entities,
                            "preserve": u.preserve}
                      for loc, u in doc.units.items()}}
    patients = {p.pid: {"pid": p.pid, "region": p.region,
                        "hospitals": sorted(p.ids), "uhids": {k: [v] for k, v in p.ids.items()},
                        "traps": p.traps} for p in world.patients}
    required = sum(u.link == "required" for d in docs for u in d.units.values())
    truth = {
        "schema": "raksha-truth/1", "generator_version": f"ghee-khatam-{GENERATOR_VERSION}",
        "seed": f"{split}:{seed}", "profile": f"generated-{split}", "chaos": split == "test",
        "ref_date": "2026-11-20", "files": files, "patients": patients,
        "traps": {"language": sorted({u.patient for d in docs if d.layout == "discharge_hi"
                                      for u in d.units.values() if u.patient}),
                  "fragmented": sorted(p.pid for p in world.patients if len(p.ids) > 1),
                  "meaning": [], "reidentification": [],
                  "homonym_pairs": world.homonym_pairs},
        "linkability": {"required_units": required},
        "canaries": [{"surface": c.full} for c in world.canaries]}
    (destination / "truth").mkdir(parents=True, exist_ok=True)
    (destination / "truth" / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


# ------------------------------------------------------------------- checks


def missing(docs: list[Doc]) -> list[tuple[str, dict]]:
    return [(d.rel, item) for d in docs for item in d.missing()]


def untracked(world: World, docs: list[Doc]) -> list[tuple[str, str]]:
    """Name words of invented people that appear in a file without an answer-key entry."""
    people = world.patients + [p.relative for p in world.patients] + \
        [s.person for s in world.staff] + world.canaries
    words = {}
    for person in people:
        for word in (person.given.latin, person.surname.latin, person.given.deva,
                     person.surname.deva):
            words.setdefault(norm(word), person)
    found = []
    for doc in docs:
        text = norm(doc.text)
        for unit in doc.units.values():
            for probe in unit.preserve:                 # doctors are kept on purpose
                text = text.replace(norm(probe["text"]), " ")
        tracked = {t for u in doc.units.values() for e in u.entities for t in e.get("tokens", [])}
        tracked |= {norm(w) for u in doc.units.values() for e in u.entities
                    for w in re.findall(r"[A-Za-z]+|[ऀ-ॿ]+", e["surface"])}
        for word in words:
            if word not in tracked and len(word) > 2 and \
                    re.search(rf"(?<![\wऀ-ॿ]){re.escape(word)}(?![\wऀ-ॿ])",
                              text):
                found.append((doc.rel, word))
    return found


# ------------------------------------------------------------------ reports


def report(docs: list[Doc], truth: dict, practice: Path | None) -> str:
    entities = [e for d in docs for u in d.units.values() for e in u.entities]
    lines = [f"files {len(docs)}, records {sum(len(d.units) for d in docs)}, "
             f"personal data pieces {len(entities)}, "
             f"kept probes {sum(len(u.preserve) for d in docs for u in d.units.values())}", ""]
    lines.append("layouts: " + ", ".join(f"{k} {v}" for k, v in
                                         sorted(Counter(d.layout for d in docs).items())))
    lines.append("")
    lines.append("coverage (type x script x where x cue):")
    cells = Counter((e["type"], e["script"], e["channel"], e["cue"]) for e in entities)
    for cell, count in sorted(cells.items()):
        lines.append(f"  {count:4}  {' / '.join(cell)}")
    traps = Counter(e.get("trap") for e in entities if e.get("trap"))
    traps.update(p.get("trap") for d in docs for u in d.units.values() for p in u.preserve
                 if p.get("trap"))
    lines.append("trap families: " + ", ".join(f"{k} {v}" for k, v in sorted(traps.items())))
    thin = [c for c, n in cells.items() if n < 3]
    lines.append(f"thin cells (fewer than 3 examples): {len(thin)}")
    if practice is not None:
        lines += [""] + novelty(docs, practice)
    return "\n".join(lines) + "\n"


def _labels(texts: list[str]) -> set[str]:
    found = set()
    for text in texts:
        for m in _LABEL_LINE.finditer(text):
            found.add(norm(m.group(1)))
        first = text.splitlines()[0] if text else ""
        if "," in first or "\t" in first:
            found |= {norm(c) for c in re.split(r"[,\t]", first) if c.strip()}
        found |= {norm(k) for k in re.findall(r'"([^"]+)"\s*:', text)}
    return found


def novelty(docs: list[Doc], practice: Path) -> list[str]:
    ours = [d.text for d in docs]
    theirs = []
    for path in sorted(practice.rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            for encoding in ("utf-8-sig", "utf-16", "cp1252"):
                try:
                    theirs.append(raw.decode(encoding))
                    break
                except UnicodeError:
                    continue
    mine, known = _labels(ours), _labels(theirs)
    words = lambda texts: Counter(w for t in texts for w in re.findall(r"\w+", norm(t)))  # noqa: E731
    lengths = lambda texts: sum(len(t.split()) for t in texts) / max(len(texts), 1)  # noqa: E731
    deva = lambda texts: sum(any("ऀ" <= c <= "ॿ" for c in t) for t in texts) \
        / max(len(texts), 1)  # noqa: E731
    vocabulary = words(ours)
    seen = words(theirs)
    unseen = sum(n for w, n in vocabulary.items() if w not in seen) / max(sum(vocabulary.values()), 1)
    return ["novelty against the practice data:",
            f"  labels, headers and keys not in the practice data: "
            f"{len(mine - known)} of {len(mine)} ({100 * len(mine - known) / max(len(mine), 1):.0f}%)",
            f"  running words not in the practice data: {100 * unseen:.0f}%",
            "realism:",
            f"  words per file: generated {lengths(ours):.0f}, practice {lengths(theirs):.0f}",
            f"  files with Devanagari: generated {100 * deva(ours):.0f}%, "
            f"practice {100 * deva(theirs):.0f}%"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", choices=("train", "test"), required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--patients", type=int, default=40)
    ap.add_argument("--toolkit")
    args = ap.parse_args()
    destination = Path(args.out).resolve()
    if ROOT == destination or destination in ROOT.parents or ROOT in destination.parents:
        raise SystemExit("refusing to write inside the repository")
    world, docs = build(args.split, args.seed, args.patients)
    wrong, loose = missing(docs), untracked(world, docs)
    if wrong or loose:
        for rel, item in wrong[:10]:
            print("not in file:", rel, item.get("type") or item.get("kind"), file=sys.stderr)
        for rel, word in loose[:10]:
            print("untracked name word:", rel, word, file=sys.stderr)
        raise SystemExit(f"answer key wrong: {len(wrong)} missing, {len(loose)} untracked")
    truth = write(world, docs, destination, args.split, args.seed)
    try:
        practice = default_truth(args.toolkit).parent.parent / "input"
    except SystemExit:
        practice = None
    text = report(docs, truth, practice if practice and practice.is_dir() else None)
    (destination / "truth" / "report.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

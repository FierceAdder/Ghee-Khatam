#!/usr/bin/env python3
"""A large invented dataset the attack replay can run on (development only).

    python3 tools/scale_attack_data.py --out <new folder> [--patients 500] [--seed 1]
    python3 tools/tune.py --truth <new folder>/truth/truth.json --secrets 10 ...

The privacy settings were chosen on 48 practice patients. This builds a training-side
generator set with many more, plus the attacker's side of it, shaped like the practice
kit's: a population registry (every patient and four other people for each) with leaked
contacts for most, and partial facts about some patients and some people who never came.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from attacker import Attacker  # noqa: E402
from generator import build as generator  # noqa: E402
from utility import ROOT  # noqa: E402

REGISTRY = ("rid", "name", "relation", "relative_name", "gender", "age", "house_no", "mohalla",
            "village", "tehsil", "district", "pin", "phone", "email", "aadhaar_last4")
OTHERS_PER_PATIENT = 4
LEAKED_SHARE = 0.6          # share of people whose contacts are in the leaked database
KNOWN_SHARE = 0.3           # share of patients the attacker knows something about
CONDITION_SHARE = 0.7       # of those facts, the share that names the condition
ABSENT_SHARE = 0.25         # extra facts about people who are in no record


def row(rid: str, person, rng: random.Random) -> dict:
    leaked = rng.random() < LEAKED_SHARE
    return {"rid": rid, "name": person.full, "relation": person.relation,
            "relative_name": person.relative.full, "gender": person.gender, "age": person.age,
            "house_no": "".join(c for c in person.house if c.isdigit()), "mohalla": "",
            "village": person.village.latin, "tehsil": "", "district": person.district[0],
            "pin": person.pin, "phone": person.phone if leaked else "", "email": "",
            "aadhaar_last4": person.aadhaar[-4:] if leaked else ""}


def write(out: Path, patients: int, seed: int) -> dict:
    world, docs = generator.build("train", seed, patients)
    generator.write(world, docs, out, "train", seed)
    rng = random.Random(f"attacker:{seed}")
    others = [world.person(f"PX{i:05d}") for i in range(patients * OTHERS_PER_PATIENT)]
    people = [(f"RID{i:07d}", p) for i, p in enumerate([*world.patients, *others])]
    rng.shuffle(people)
    folder = out / "attacker"
    folder.mkdir(parents=True)
    with open(folder / "registry.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, REGISTRY)
        writer.writeheader()
        writer.writerows(row(rid, p, rng) for rid, p in people)
    rid_of = {p.pid: rid for rid, p in people}
    truth_path = out / "truth" / "truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    for pid, info in truth["patients"].items():
        info["rid"] = rid_of[pid]
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")

    # what the attacker knows is taken from what the raw records show about each patient
    facts_path = folder / "known_facts.jsonl"
    facts_path.write_text("", encoding="utf-8")
    attacker = Attacker(ROOT, truth_path)
    dates: dict[str, set] = {}
    for record in attacker.records(attacker.raw):
        if record["patient"]:
            dates.setdefault(record["patient"], set()).update(
                d for d in record["evidence"].dates if d.year >= 2026)
    names = {h.code: h.name for h in world.hospitals}
    known = sorted(p for p in dates if dates[p])
    facts = []
    for pid in rng.sample(known, round(len(known) * KNOWN_SHARE)):
        patient = next(p for p in world.patients if p.pid == pid)
        fact = {"rid": rid_of[pid], "visit_dates": [rng.choice(sorted(dates[pid])).isoformat()],
                "hospital": names[rng.choice(sorted(patient.ids))]}
        if rng.random() < CONDITION_SHARE:
            fact["condition_hint"] = rng.choice(patient.diagnoses)[0]
        facts.append(fact)
    every = sorted(d for found in dates.values() for d in found)
    for person in rng.sample(others, round(len(facts) * ABSENT_SHARE)):
        facts.append({"rid": rid_of[person.pid], "visit_dates": [rng.choice(every).isoformat()],
                      "hospital": rng.choice(sorted(names.values()))})
    facts_path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in facts),
                          encoding="utf-8")
    return {"patients": len(world.patients), "registry": len(people), "facts": len(facts),
            "files": len(docs)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--patients", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if args.seed in (7, 8, 9):
        ap.error("seeds 7-9 are reserved for the final numbers")
    if out.exists() or out == ROOT or ROOT in out.parents:
        ap.error("--out must be a new folder outside the repository")
    print(write(out, args.patients, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

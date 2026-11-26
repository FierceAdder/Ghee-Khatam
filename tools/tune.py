#!/usr/bin/env python3
"""Compare privacy-policy settings by attack risk and utility (development only).

    python3 tools/tune.py age_band_years=5,10 pin_prefix_digits=0,3

Every combination of the listed values is run on the practice data and scored with
``attacker.py`` and ``utility.py``. ``--variants`` repeats each measurement with a
stricter and a looser attacker, so a setting is not tuned to one reading of the attack.
"""

from __future__ import annotations

import argparse
import io
import itertools
import tempfile
from dataclasses import fields, replace
from pathlib import Path

from attacker import Attacker, Settings, attack
from utility import ROOT, default_truth

from wards.pipeline import Pipeline
from wards.policy import Policy, load_policy
from wards.security import SafeLog, Secret

VARIANTS = {
    "published": Settings(),
    "rare<=5": Settings(rare_cutoff=5),
    "confident>=0.3": Settings(confident=0.3),
    "stay +-1": Settings(stay_tolerance=1),
}


def parse(options: list[str]) -> list[dict]:
    kinds = {f.name: str(f.type) for f in fields(Policy)}
    axes = []
    for option in options:
        name, _, values = option.partition("=")
        if name not in kinds or not values:
            raise SystemExit("unknown policy setting")
        cast = int if kinds[name] == "int" else str
        axes.append([(name, cast(v)) for v in values.split(",")])
    return [dict(combination) for combination in itertools.product(*axes)] or [{}]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("settings", nargs="*", help="name=value1,value2")
    ap.add_argument("--toolkit")
    ap.add_argument("--truth")
    ap.add_argument("--variants", action="store_true")
    ap.add_argument("--secrets", type=int, default=3,
                    help="average over this many secrets (date shifts differ between them)")
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    source = truth.parent.parent / "input"
    base = load_policy(ROOT / "config" / "policy.yaml")
    attackers = {}
    for name, settings in VARIANTS.items():
        if name == "published" or args.variants:
            attacker = Attacker(truth.parents[2], truth, settings)
            attackers[name] = (attacker, attacker.measure(attacker.raw))

    header = "setting" + " " * 45 + "A2/A3     A4     A5   util  priv  score"
    print(header + ("   worst-case score" if args.variants else ""))
    for overrides in parse(args.settings):
        totals: dict[str, float] = {}
        worst: dict[str, float] = {}
        for number in range(args.secrets):
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "release"
                pipeline = Pipeline(replace(base, **overrides).validate(),
                                    Secret(f"tune-{number}".encode()), SafeLog(io.StringIO()))
                pipeline.run(source, output)
                for name, (attacker, raw) in attackers.items():
                    result = attack(truth, output, attacker=attacker, raw=raw)
                    if name == "published":
                        row = {**result["risks"], "utility": result["utility"],
                               "privacy": result["privacy"], "score": result["score"]}
                        for key, value in row.items():
                            totals[key] = totals.get(key, 0.0) + value / args.secrets
                    worst[name] = worst.get(name, 0.0) + result["score"] / args.secrets
        label = " ".join(f"{k}={v}" for k, v in overrides.items()) or "(config/policy.yaml)"
        line = f"{label:<52}{totals['A2/A3']:5.3f}  {totals['A4']:5.3f}  {totals['A5']:5.3f}  " \
               f"{totals['utility']:5.3f} {totals['privacy']:5.3f}  {totals['score']:5.2f}"
        if args.variants:
            line += f"   {min(worst.values()):5.2f}"
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

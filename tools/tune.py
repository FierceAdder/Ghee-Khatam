#!/usr/bin/env python3
"""Compare privacy-policy settings by attack risk and utility (development only).

    python3 tools/tune.py age_band_years=5,10 pin_prefix_digits=0,3
    python3 tools/tune.py --reference-like
    python3 tools/tune.py --targets

Every combination of the listed values is run on the practice data and scored with
``attacker.py`` and ``utility.py``. The score is the mean over the secrets; ``lowest`` is
the worst single secret. ``--variants`` repeats each measurement with a stricter and a
looser attacker, so a setting is not tuned to one reading of the attack.
``--reference-like`` first applies, in memory only, the settings of the organisers'
reference system (5-year age bands, district kept, per-patient date shifts), to compare
the replay with the readings they publish for it. ``--targets`` breaks the targeted
attack down by the signal that carries it.
"""

from __future__ import annotations

import argparse
import io
import itertools
import tempfile
from collections import Counter
from dataclasses import fields, replace
from pathlib import Path

from attacker import Attacker, Settings, attack
from utility import ROOT, Release, default_truth

from wards.pipeline import Pipeline
from wards.policy import Policy, load_policy
from wards.security import SafeLog, Secret

# What ``docs/ATTACKS.md`` says of the reference: "5-year bands, district kept, per-patient
# shifts" and no protection of rare diagnoses. District and per-patient shifts are ours too.
REFERENCE_LIKE = {"age_band_years": 5, "age_band_max_years": 5, "age_partitioning": "global",
                  "min_group_size": 1, "small_group_percent": 100, "l_diversity": 0,
                  "rare_diagnosis_patients": 0}
# Its published readings: record linkage relative to raw, the other two as they stand.
REFERENCE_READINGS = {"A2/A3 vs raw": 0.277, "A4": 0.167, "A5": 0.401}
SIGNALS = ("date", "condition", "stay", "hospital")

VARIANTS = {
    "published": Settings(),
    "reads codes": Settings(condition_codes=True),
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


def targeted_breakdown(attacker: Attacker, release: Release) -> dict:
    """One release's targets: each one's trace, success and success with signals left out."""
    profiles = attacker.profiles(attacker.records(release))
    traces: dict[str, list] = {}
    cases = {"all": frozenset(), **{f"without {s}": frozenset({s}) for s in SIGNALS},
             **{f"only {s}": frozenset(SIGNALS) - {s} for s in SIGNALS[:3]}}
    for name, ignore in cases.items():
        traces[name] = []
        attacker.targeted(profiles, ignore=ignore, trace=traces[name])
    return traces


def print_breakdown(runs: list[dict]) -> None:
    """Targeted success over several releases, by signal."""
    targets = len(runs[0]["all"])
    total = sum(t["success"] for run in runs for t in run["all"])
    print(f"targeted attack: {targets} targeted patients x {len(runs)} secrets, "
          f"mean success {total / (targets * len(runs)):.3f}")
    print("target  fact holds                     consistent  matching  success  "
          "right-profile signals (share of success)")
    combos: Counter = Counter()
    for number in range(targets):
        rows = [run["all"][number] for run in runs]
        mean = {key: sum(r[key] for r in rows) / len(rows)
                for key in ("consistent", "success")}
        own: Counter = Counter()
        for row in rows:
            for match in row["matches"]:
                if match["right"]:
                    own["+".join(match["signals"])] += match["share"] / len(rows)
        combos.update(own)
        holds = "+".join(rows[0]["holds"]) + (" (rare)" if rows[0]["rare"] else "")
        shown = ", ".join(f"{k} {v:.2f}" for k, v in sorted(own.items(), key=lambda i: -i[1]))
        print(f"{number:>6}  {holds:<30} {mean['consistent']:10.1f}  "
              f"{sum(len(r['matches']) for r in rows) / len(rows):8.1f}  "
              f"{mean['success']:7.3f}  {shown}")
    success = total / len(runs)
    print("\nshare of success by the signals matched on the right profile:")
    for combo, mass in sorted(combos.items(), key=lambda i: (-i[1], i[0])):
        print(f"  {combo:<36} {mass * len(runs) / total if total else 0:6.1%}")
    print("\nsignal      involved  success without it  drop     success with it alone "
          "(hospital kept off)")
    for signal in SIGNALS:
        involved = sum(mass for combo, mass in combos.items() if signal in combo.split("+"))
        without = sum(t["success"] for run in runs for t in run[f"without {signal}"]) / len(runs)
        only = [t["success"] for run in runs for t in run.get(f"only {signal}", [])]
        alone = f"{sum(only) / len(only):.3f}" if only else ""
        print(f"  {signal:<10}{involved / success if success else 0:8.1%}  "
              f"{without / targets:18.3f}  {(success - without) / targets:+.3f}  {alone:>8}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("settings", nargs="*", help="name=value1,value2")
    ap.add_argument("--toolkit")
    ap.add_argument("--truth")
    ap.add_argument("--variants", action="store_true")
    ap.add_argument("--secrets", type=int, default=3,
                    help="average over this many secrets (date shifts differ between them)")
    ap.add_argument("--reference-like", action="store_true",
                    help="apply the organisers' reference settings (in memory only)")
    ap.add_argument("--targets", action="store_true",
                    help="break the targeted attack down by signal")
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    source = truth.parent.parent / "input"
    base = load_policy(ROOT / "config" / "policy.yaml")
    attackers = {}
    for name, settings in VARIANTS.items():
        if name == "published" or args.variants:
            attacker = Attacker(truth.parents[2], truth, settings)
            attackers[name] = (attacker, attacker.measure(attacker.raw))

    header = "setting" + " " * 45 + "A2/A3     A4     A5   util  priv  score  lowest"
    for overrides in parse(args.settings):
        if args.reference_like:
            overrides = {**REFERENCE_LIKE, **overrides}
        totals: dict[str, float] = {}
        worst: dict[str, float] = {}
        lowest = float("inf")
        breakdowns = []
        for number in range(args.secrets):
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "release"
                pipeline = Pipeline(replace(base, **overrides).validate(),
                                    Secret(f"tune-{number}".encode()), SafeLog(io.StringIO()))
                pipeline.run(source, output)
                for name, (attacker, raw) in attackers.items():
                    result = attack(truth, output, attacker=attacker, raw=raw)
                    if name == "published":
                        lowest = min(lowest, result["score"])
                        row = {**result["risks"], "utility": result["utility"],
                               "privacy": result["privacy"], "score": result["score"],
                               **{f"{k} itself": v for k, v in result["absolute"].items()}}
                        for key, value in row.items():
                            totals[key] = totals.get(key, 0.0) + value / args.secrets
                    worst[name] = worst.get(name, 0.0) + result["score"] / args.secrets
                if args.targets:
                    breakdowns.append(targeted_breakdown(attackers["published"][0],
                                                         Release(truth, output)))
        label = " ".join(f"{k}={v}" for k, v in overrides.items()) or "(config/policy.yaml)"
        if args.reference_like:
            label = "reference-like"
        print(header + ("   worst variant" if args.variants else ""))
        line = f"{label:<52}{totals['A2/A3']:5.3f}  {totals['A4']:5.3f}  {totals['A5']:5.3f}  " \
               f"{totals['utility']:5.3f} {totals['privacy']:5.3f}  {totals['score']:5.2f}" \
               f"  {lowest:6.2f}"
        if args.variants:
            line += f"   {min(worst.values()):5.2f}"
        print(line, flush=True)
        if args.reference_like:
            ours = {"A2/A3 vs raw": totals["A2/A3"], "A4": totals["A4 itself"],
                    "A5": totals["A5 itself"]}
            print("reference readings   published (their dataset)   replay (practice)")
            for name, published in REFERENCE_READINGS.items():
                print(f"  {name:<18} {published:12.3f} {ours[name]:22.3f}   "
                      f"{ours[name] - published:+.3f}")
        if args.targets:
            print_breakdown(breakdowns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

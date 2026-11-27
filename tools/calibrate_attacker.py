#!/usr/bin/env python3
"""Compare our attack replay with the organisers' result on the raw practice input.

    python3 tools/calibrate_attacker.py [--toolkit ../Secret-Wards-Toolkit] [--check]

The official privacy score divides each attack by the same attack on the raw input, so a
replay that reads the raw input differently from the organisers cannot be trusted to rank
privacy settings. ``dev/truth/raw_attack.json`` holds their numbers; every component is
printed beside ours. With ``--check`` the exit code is 1 when a component that enters the
score, or a targeted signal-hit count, is outside the tolerance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from attacker import Attacker, Settings
from utility import default_truth

RATE_TOLERANCE = 0.05
HIT_TOLERANCE = 2
# official name, our name, tolerance (None: shown only, never fails the check)
ROWS = (
    ("records_attacked", "records_attacked", 0),
    ("patients_present", "patients_present", 0),
    ("clusters", "profiles", 0),
    ("targets", "targets", 0),
    ("direct_exposure", "A1", RATE_TOLERANCE),
    ("record_linkage_risk", "A2/A3", RATE_TOLERANCE),
    ("patients_reidentified", "A4", RATE_TOLERANCE),
    ("targeted_success", "A5", RATE_TOLERANCE),
    ("pseudonym_inversion", "A6", RATE_TOLERANCE),
    ("recovered_global_shift_days", "shift", 0),
    ("decoy_false_claims", "decoy_claims", 0),
    # Their definitions are not published and neither enters the score: ours are guesses.
    ("unique_records", "unique_records", None),
    ("confident_record_reid", "confident_records", None),
    ("attribute_disclosure", "attribute_disclosure", None),
)
SIGNALS = ("date", "condition", "stay")


def official(truth: Path) -> dict:
    result = json.loads((truth.parent / "raw_attack.json").read_text(encoding="utf-8"))["result"]
    result["pseudonym_inversion"] = result["pseudonym_inversion"]["rate"]
    return result


def compare(theirs: dict, ours: dict) -> list[tuple[str, float, float | None, str]]:
    """``(component, official, replay, verdict)`` rows; "OFF" fails the check."""
    rows = []
    for name, key, tolerance in ROWS:
        value = ours.get(key)
        if value is None:
            verdict = "not modelled"
        elif abs(value - theirs[name]) <= (RATE_TOLERANCE if tolerance is None else tolerance):
            verdict = "ok"
        else:
            verdict = "differs (not scored)" if tolerance is None else "OFF"
        rows.append((name, theirs[name], value, verdict))
    for signal in SIGNALS:
        want, got = theirs["targeted_signal_hits"][signal], ours["hits"].get(signal, 0)
        rows.append((f"signal hits: {signal}", want, got,
                     "ok" if abs(got - want) <= HIT_TOLERANCE else "OFF"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--toolkit")
    ap.add_argument("--truth")
    ap.add_argument("--codes", action="store_true",
                    help="show the stronger condition matching (ICD codes, non-English names)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    truth = Path(args.truth) if args.truth else default_truth(args.toolkit)
    attacker = Attacker(truth.parents[2], truth, Settings(condition_codes=args.codes))
    found = attacker.measure(attacker.raw)
    rows = compare(official(truth), found)
    print(f"{'component':<30}{'official':>10}{'replay':>10}{'difference':>12}   verdict")
    for name, want, got, verdict in rows:
        shown = "-" if got is None else f"{got:.3f}"
        gap = "" if got is None else f"{got - want:+.3f}"
        print(f"{name:<30}{want:>10.3f}{shown:>10}{gap:>12}   {verdict}")
    print(f"record linkage from each record's own evidence only: "
          f"{found['linkage_record_only']:.3f}")
    return 1 if args.check and any(row[3] == "OFF" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

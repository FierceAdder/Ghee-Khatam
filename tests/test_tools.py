"""The measuring tools: utility estimate and attack replay.

Skipped without the practice kit, because both read its registry and truth format.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))

TOOLKIT = Path(os.environ.get("TOOLKIT_DIR", ROOT.parent / "Secret-Wards-Toolkit"))
TRUTH = TOOLKIT / "dev" / "truth" / "truth.json"

pytestmark = pytest.mark.skipif(not TRUTH.is_file(), reason="practice kit not available")


@pytest.fixture(scope="module")
def attacker():
    from attacker import Attacker

    return Attacker(TOOLKIT, TRUTH)


@pytest.mark.parametrize("value, true, score", [
    ("47", 47, 1.0), ("45-49", 47, 0.9), ("40-49", 47, 0.8), ("45-59", 47, 0.6),
    ("30-59", 47, 0.3), ("50-54", 47, 0.0), ("90+", 97, 0.9), ("90+", 80, 0.0),
    ("[AGE]", 47, 0.0),
])
def test_age_scoring_follows_the_published_formula(value, true, score):
    from utility import _age_score

    assert _age_score(value, true) == score


@pytest.mark.parametrize("released, matches", [
    ({"B55.0"}, True), ({"B55"}, True), ({("B50", "B64")}, True), ({("A00", "B99")}, True),
    ({"B54"}, False), ({("C00", "D48")}, False), (set(), False),
])
def test_generalised_codes_still_match_for_the_attacker(released, matches):
    from attacker import Evidence, _has_condition

    assert _has_condition(Evidence(conditions=released), "B55.0") is matches


def test_durations_and_lab_counts_are_not_demographics(attacker):
    evidence = attacker.extract(
        "history: K/C/O DM2 x 8 yrs, Platelet 229000 /uL, Distt. Mainpuri", "dmy", "")
    assert evidence.ages == set() and evidence.pins == set()


def test_released_demographics_are_read(attacker):
    evidence = attacker.extract(
        "Age/Sex: 45-59 Yrs/F | PIN: 229*** | admitted 03/03/2026 discharged 12/03/2026",
        "dmy", "arogya")
    assert evidence.ages == {(45, 59)} and evidence.genders == {"F"}
    assert evidence.pins == {"229***"} and evidence.stays == {9}
    assert evidence.hospitals == {"Arogya Seva Sadan"}


def test_raw_input_is_attacked_about_as_well_as_published(attacker):
    """The organisers report A4 0.98 and A5 1.00 on the raw practice input."""
    raw = attacker.measure(attacker.raw)
    assert raw["A1"] == 1.0
    assert raw["A4"] >= 0.9 and raw["A5"] >= 0.9
    assert raw["A2/A3"] >= 0.6


def test_policy_sweep_rejects_unknown_settings():
    from tune import parse

    assert parse(["age_band_years=5,10"]) == [{"age_band_years": 5}, {"age_band_years": 10}]
    with pytest.raises(SystemExit):
        parse(["nonsense=1"])


# ------------------------------------------------------------- altered copies
# Only the levels used for development are exercised; the held-out level is measured once.


def altered(destination: Path, level: str, seed: int):
    from mutate import Mutator

    truth = __import__("json").loads(TRUTH.read_text(encoding="utf-8"))
    Mutator(truth, seed, level).run(TRUTH.parent.parent / "input", destination)
    return destination


@pytest.mark.parametrize("level", ["easy", "hard"])
def test_an_altered_copy_has_an_exact_answer_key(tmp_path, level):
    from mutate import Mutator

    missing, total = Mutator.unmatched(altered(tmp_path / level, level, 7))
    assert total > 2000 and missing == 0


def test_altered_copies_repeat_for_a_seed_and_differ_between_seeds(tmp_path):
    def contents(root: Path) -> dict:
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()}

    first = contents(altered(tmp_path / "a", "easy", 7))
    assert contents(altered(tmp_path / "b", "easy", 7)) == first
    assert contents(altered(tmp_path / "c", "easy", 8)) != first


@pytest.mark.parametrize("destination", [str(ROOT), str(ROOT.parent), str(TOOLKIT)])
def test_altered_copies_never_overwrite_sources(monkeypatch, destination):
    import mutate

    monkeypatch.setattr(sys, "argv", ["mutate.py", "--out", destination,
                                      "--toolkit", str(TOOLKIT)])
    with pytest.raises(SystemExit, match="refusing"):
        mutate.main()

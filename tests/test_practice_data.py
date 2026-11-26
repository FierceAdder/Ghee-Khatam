"""Checks against the organisers' practice kit. Skipped when the kit is not present.

Set ``TOOLKIT_DIR`` to point at it (default: ``../Secret-Wards-Toolkit``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from conftest import ROOT

TOOLKIT = Path(os.environ.get("TOOLKIT_DIR", ROOT.parent / "Secret-Wards-Toolkit"))
TRUTH = TOOLKIT / "dev" / "truth" / "truth.json"

pytestmark = pytest.mark.skipif(not TRUTH.is_file(), reason="practice kit not available")


@pytest.fixture(scope="module")
def truth() -> dict:
    return json.loads(TRUTH.read_text(encoding="utf-8"))


def _shipped_files():
    """Everything of ours that goes into the repository, except the team's planning notes."""
    folders = ("wards", "resources", "config", "tools", "tests", "docs")
    files = [p for name in folders for p in sorted((ROOT / name).rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
             and p.suffix != ".pdf"]      # a PDF is made from a source that is scanned
    return files + [p for p in (ROOT / "main.py", ROOT / "README.md", ROOT / "SCORES.md")
                    if p.is_file()]


def test_repository_holds_no_practice_identities(truth):
    """No practice patient's full name or identifier in code, gazetteers, tests or docs."""
    needles = set()
    for spec in truth["files"].values():
        for unit in spec["units"].values():
            for entity in unit["entities"]:
                surface = unicodedata.normalize("NFKC", entity["surface"]).casefold()
                if entity["type"] in ("PATIENT_NAME", "RELATIVE_NAME", "CANARY"):
                    if len(surface.split()) >= 2:       # single name words are general
                        needles.add((entity["type"], surface, False))
                elif entity.get("match") in ("digits", "digits_exact"):
                    if len(str(entity["key"])) >= 6:
                        needles.add((entity["type"], str(entity["key"]), True))
                elif entity["type"] in ("PAN", "EMAIL", "INSURANCE_ID", "AADHAAR_MASKED"):
                    needles.add((entity["type"], surface, False))
    offenders = set()
    for path in _shipped_files():
        # binary files (trained models) are searched too, for any text they hold
        raw = path.read_bytes().decode("utf-8", errors="ignore")
        text = unicodedata.normalize("NFKC", raw).casefold()
        joined = re.sub(r"(?<=\d)[ \-](?=\d)", "", text)
        for kind, needle, digits in sorted(needles):
            if needle not in (joined if digits else text):
                continue                        # a cheap test first: models are large
            edge = r"\d" if digits else r"[a-z\u0900-\u097f]"
            if re.search(rf"(?<!{edge}){re.escape(needle)}(?!{edge})",
                         joined if digits else text):
                offenders.add((path.relative_to(ROOT).as_posix(), kind))
    assert offenders == set()


@pytest.fixture(scope="module")
def practice_output(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("practice")
    env = dict(os.environ, RAKSHA_SECRET="practice-test")
    run = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--input", str(TOOLKIT / "dev" / "input"),
         "--output", str(out / "release")],
        env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    return out / "release"


def toolkit_script(name: str, *args: str) -> str:
    run = subprocess.run([sys.executable, str(TOOLKIT / name), *args], capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout


def test_practice_output_meets_the_contract(practice_output):
    out = toolkit_script("validate_output.py", "--input", str(TOOLKIT / "dev" / "input"),
                         "--output", str(practice_output))
    assert "CONTRACT OK" in out and "WARN" not in out


def test_practice_regression_floor(practice_output):
    """A floor, not a target: fails if a change loses ground on the practice data."""
    out = toolkit_script("check_dev.py", "--output", str(practice_output))
    leaked = int(re.search(r"PERSONAL DATA: (\d+) of", out).group(1))
    canaries = int(re.search(r"CANARIES: (\d+) of", out).group(1))
    kept = float(re.search(r"survived \(\s*([\d.]+)%", out).group(1))
    precision, recall = map(float, re.search(r"precision ([\d.]+), recall ([\d.]+)", out).groups())
    raw = int(re.search(r"RAW COPIES: (\d+)", out).group(1))
    assert leaked <= 5 and canaries == 0 and raw == 0
    assert kept >= 99.0
    assert precision >= 0.98 and recall >= 0.95

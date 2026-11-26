#!/usr/bin/env python3
"""Switch off one family of detection evidence at a time and count what leaks (development only).

    python3 tools/ablate.py [--copy DIR ...] [--family NAME ...]

``DIR`` is a folder with ``input/`` and ``truth/truth.json`` (an altered or scrambled copy);
without ``--copy`` the practice data is used. For every family the firewall runs with that
evidence removed and the toolkit's ``check_dev.py`` counts the leaks. A family whose removal
changes nothing is not what protects that data.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from utility import ROOT, default_truth

from wards import columns, documents
from wards.audit import Auditor
from wards.detect.engine import Detector
from wards.knowledge import Knowledge
from wards.lexicon import Lexicon
from wards.model import NAME_TYPES
from wards.pipeline import Pipeline
from wards.policy import load_policy
from wards.security import SafeLog, Secret
from wards.sweep import NameSweeper

# Name sources (``Mention.source``) grouped by the kind of evidence they use.
NAME_SOURCES = {
    "labels": {"patient_label", "relative_label", "spoken_name", "patient_word"},
    "relations": {"relation", "relation_subject", "care_of", "role_after", "acted_by"},
    "honorifics and titles": {"honorific", "title"},
    "grammar and layout": {"postposition", "possessive", "report_of", "before_age",
                           "time_entry", "before_phone"},
    "chat senders": {"chat_sender"},
    "gazetteer phrase runs": {"gazetteer"},
    "file names and keys": {"path"},
    "typed name fields": {"field"},
}


def _drop_names(sources: set[str]):
    original = Detector.detect_segment

    def detect(self, segment):
        return [m for m in original(self, segment)
                if not (m.etype in NAME_TYPES and m.source in sources)]

    return mock.patch.object(Detector, "detect_segment", detect)


def _patches(family: str) -> list:
    if family in NAME_SOURCES:
        return [_drop_names(NAME_SOURCES[family])]
    if family == "name lists":
        return [mock.patch.object(Lexicon, "is_first_name", lambda self, t: False),
                mock.patch.object(Lexicon, "is_surname", lambda self, t: False)]
    if family == "value typing":
        nothing = lambda *args, **kwargs: None  # noqa: E731
        return [mock.patch.object(columns, "infer_from_values", nothing),
                mock.patch.object(documents, "infer_from_values", nothing)]
    if family == "name sweep":
        return [mock.patch.object(NameSweeper, "sweep", lambda self, s, bag: [])]
    if family == "identifier sweep":
        return [mock.patch.object(Knowledge, "sweep", lambda self, s, p: [])]
    if family == "self-scan":
        return [mock.patch.object(Auditor, "repair", lambda self, doc: 0),
                mock.patch.object(Auditor, "leaks", lambda self, text: [])]
    if family == "nothing":
        return []
    raise SystemExit(f"unknown family: {family}")


FAMILIES = ["nothing", *NAME_SOURCES, "name lists", "value typing", "name sweep",
            "identifier sweep", "self-scan"]


def measure(copy: Path, family: str, toolkit: Path) -> tuple[int, int, int]:
    """(pieces leaked, names leaked, canaries found) with ``family`` switched off."""
    with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
        for patch in _patches(family):
            stack.enter_context(patch)
        output = Path(folder) / "release"
        Pipeline(load_policy(ROOT / "config" / "policy.yaml"), Secret(b"ablation-secret"),
                 SafeLog(io.StringIO())).run(copy / "input", output)
        report = subprocess.run(
            [sys.executable, "-B", str(toolkit / "check_dev.py"), "--output", str(output),
             "--truth", str(copy / "truth" / "truth.json"), "--show", "100000"],
            capture_output=True, text=True, check=True).stdout
    leaked = int(re.search(r"PERSONAL DATA: (\d+) of", report).group(1))
    canaries = int(re.search(r"CANARIES: (\d+) of", report).group(1))
    names = len(re.findall(r"(?m)^    (?:PATIENT_NAME|RELATIVE_NAME) \|", report))
    return leaked, names, canaries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--copy", action="append", default=[])
    ap.add_argument("--family", action="append", default=[])
    ap.add_argument("--toolkit")
    args = ap.parse_args()
    truth = default_truth(args.toolkit)
    toolkit = truth.parents[2]
    copies = [Path(c) for c in args.copy] or [truth.parents[1]]
    families = args.family or FAMILIES
    print(f"{'switched off':<24}" + "".join(f"{c.name[:22]:>26}" for c in copies))
    print(" " * 24 + "   leaked / names / canary" * len(copies))
    for family in families:
        cells = []
        for copy in copies:
            leaked, names, canaries = measure(copy, family, toolkit)
            cells.append(f"{leaked:>15} / {names:>5} / {canaries:>2}")
        print(f"{family:<24}" + "".join(f"{c:>26}" for c in cells), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

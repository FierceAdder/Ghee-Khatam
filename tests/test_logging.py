"""The run log: a closed schema, no input values, a tamper-evident chain, and the gate.

All names and numbers here are invented.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from conftest import ROOT, write_tree

from wards import logschema
from wards import pipeline as pipeline_module
from wards.pipeline import Pipeline
from wards.security import SafeLog, Secret, verify_log

SECRET = b"unit-test-secret"
PLANTED = ("Meren Longkumer", "9812345670", "4821 7365 9012", "QXR/26/004411",
           "meren.l@example.org", "Devipur")
FILES = {
    "reg.csv": "UHID,Patient Name,Mobile,Aadhaar,Email,Village\n"
               "QXR/26/004411,Meren Longkumer,9812345670,4821 7365 9012,"
               "meren.l@example.org,Devipur\n",
    "note.txt": "Patient: Meren Longkumer, UHID QXR/26/004411. BP 120/80. Dolo 650 BD.\n",
    "broken.json": '{"patient": "Meren',
}


def strict_log(level=logschema.SUMMARY) -> SafeLog:
    return SafeLog(io.StringIO(), level=level, strict=True)


# ------------------------------------------------------------------- schema


@pytest.mark.parametrize("event, fields", [
    ("no_such_event", {}),
    ("stage", {"stage": "parse", "patient": 1}),                     # unknown field
    ("stage", {"stage": "Meren Longkumer"}),                         # not a known stage
    ("problem", {"stage": "parse", "reason": "phone 9812345670"}),  # free text as a reason
    ("problem", {"stage": "parse", "error": "ValueError('Meren')"}),
    ("run_started", {"files": "3"}),                                 # number as text
    ("run_started", {"files": True}),
    ("run_started", {"files": 3, "policy": "not hex"}),
])
def test_anything_outside_the_schema_is_refused(event, fields):
    with pytest.raises(TypeError):
        strict_log().record(event, **fields)


def test_a_real_run_drops_and_counts_instead_of_failing():
    log = SafeLog(io.StringIO())
    log.record("stage", stage="Meren Longkumer")
    assert log.dropped == 1
    assert "Meren" not in log.sealed(Secret(SECRET), "ab" * 32)


def test_detail_events_only_at_detail_level():
    summary, detail = strict_log(), strict_log(logschema.DETAIL)
    for log in (summary, detail):
        log.record("file", file=0, status="sanitized", records=1, repairs=0, renamed=0)
    assert '"file"' not in summary.sealed(Secret(SECRET), "00")
    assert '"event":"file"' in detail.sealed(Secret(SECRET), "00")


def _literals(pattern: str) -> set[str]:
    found = set()
    for path in sorted((ROOT / "wards").rglob("*.py")):
        found |= set(re.findall(pattern, path.read_text(encoding="utf-8")))
    return found


def test_every_reason_in_the_code_is_declared():
    reasons = _literals(r'QuarantineError\("([a-z_]+)"\)') \
        | _literals(r'reason="([a-z_]+)"') \
        | _literals(r'found\.append\("([a-z_]+)"\)') | _literals(r'return \["([a-z_]+)"\]')
    assert reasons and reasons <= logschema.REASONS


# -------------------------------------------------------------- chain


def sealed_text() -> str:
    log = strict_log()
    log.record("run_started", files=2, policy="ab12")
    log.record("stage", stage="parse", seconds=0.5, peak_rss_mb=40.0, files=2)
    return log.sealed(Secret(SECRET), "cd" * 32)


@pytest.mark.parametrize("tamper", [
    lambda lines: lines,                                             # control: intact
    lambda lines: [lines[0].replace('"files":2', '"files":3'), *lines[1:]],
    lambda lines: lines[1:],                                         # first line removed
    lambda lines: lines[:-1],                                        # seal removed
    lambda lines: [lines[1], lines[0], *lines[2:]],                  # reordered
    lambda lines: [*lines[:2], lines[1], *lines[2:]],                # line repeated
])
def test_the_chain_detects_any_edit(tamper):
    lines = sealed_text().splitlines()
    intact = tamper(lines) == lines
    assert verify_log("\n".join(tamper(lines)) + "\n", Secret(SECRET)) is intact


def test_another_secret_cannot_verify_or_forge():
    assert not verify_log(sealed_text(), Secret(b"another-secret"))


# -------------------------------------------------------- whole runs


def run(tmp_path, policy, lexicon, level=logschema.SUMMARY, files=FILES):
    source = write_tree(tmp_path / "in", files)
    out = tmp_path / "out"
    log = SafeLog(io.StringIO(), level=level)
    Pipeline(policy, Secret(SECRET), log, lexicon).run(source, out)
    return out, log


@pytest.mark.parametrize("level", [logschema.SUMMARY, logschema.DETAIL])
def test_a_run_publishes_a_clean_verifiable_log(tmp_path, policy, lexicon, level):
    out, log = run(tmp_path, policy, lexicon, level)
    text = (out / "logs" / "run.jsonl").read_text(encoding="utf-8")
    assert log.dropped == 0 and verify_log(text, Secret(SECRET))
    assert all(value not in text for value in PLANTED)
    assert "reg.csv" not in text and "note.txt" not in text          # files by number only
    events = [json.loads(line)["event"] for line in text.splitlines()]
    assert events[0] == "run_started" and events[-2:] == ["log", "seal"]
    assert "linking" in events and "privacy" in events and "self_scan" in events
    if level == logschema.DETAIL:
        quarantined = [json.loads(line) for line in text.splitlines()
                       if '"status":"quarantined"' in line]
        assert [q["reason"] for q in quarantined] == ["malformed_json"]


def test_the_seal_binds_the_report(tmp_path, policy, lexicon):
    import hashlib

    out, _ = run(tmp_path, policy, lexicon)
    seal = json.loads((out / "logs" / "run.jsonl").read_text().splitlines()[-1])
    assert seal["report"] == hashlib.sha256((out / "report.json").read_bytes()).hexdigest()


def test_a_failed_run_leaves_a_failed_log(tmp_path, policy, lexicon, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("Meren Longkumer 9812345670")

    monkeypatch.setattr(pipeline_module.Linker, "link", explode)
    with pytest.raises(RuntimeError):
        run(tmp_path, policy, lexicon)
    text = (tmp_path / "out" / "logs" / "run.jsonl").read_text(encoding="utf-8")
    assert verify_log(text, Secret(SECRET))
    assert '"status":"failed"' in text and '"error":"RuntimeError"' in text
    assert all(value not in text for value in PLANTED)


def test_a_log_that_names_someone_is_withheld(tmp_path, policy, lexicon, monkeypatch):
    real = SafeLog.sealed
    monkeypatch.setattr(SafeLog, "sealed",
                        lambda self, secret, digest: real(self, secret, digest)
                        + '{"note":"Meren Longkumer"}\n')
    out, _ = run(tmp_path, policy, lexicon)
    assert not (out / "logs" / "run.jsonl").exists()
    assert (out / "report.json").exists()                     # the release still goes out


def test_linking_decisions_are_counted(tmp_path, policy, lexicon):
    files = {"a.csv": "UHID,Patient Name,Sex\nQXR/26/004411,Meren Longkumer,F\n",
             "b.csv": "UHID,Patient Name,Sex\nQXR/26/004411,Meren Longkumer,M\n"}
    out, _ = run(tmp_path, policy, lexicon, files=files)
    linking = next(json.loads(line) for line in
                   (out / "logs" / "run.jsonl").read_text().splitlines() if '"linking"' in line)
    assert linking["refused_sex"] == 1 and linking["merged_strong"] == 0


def test_exposure_counts_match_a_hand_count(policy, lexicon):
    class Plan:
        rare = frozenset({"B55.0"})
        holders = {"B55.0": {"p1"}, "A90": {"p1", "p2"}, "J18.9": {"p1", "p2", "p3", "p4"}}

    log = strict_log()
    pipeline = Pipeline(policy, Secret(SECRET), log, lexicon)
    pipeline.privacy.band_width = 15
    pipeline._demographics = [("Sitapur", "F", 47), ("Sitapur", "F", 50),
                              ("Sitapur", "M", 47), ("Unnao", "F", 30)]
    pipeline._log_privacy(Plan())
    line = json.loads(log.sealed(Secret(SECRET), "00").splitlines()[0])
    # 45-59/F/Sitapur is shared by two; the other two patients are alone in their group
    assert line["unique_band_sex_district"] == 2
    assert line["patients_with_code_held_by_3_or_fewer"] == 2      # A90: p1 and p2


def test_verify_log_tool(tmp_path, policy, lexicon, monkeypatch, capsys):
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    import verify_log as tool

    out, _ = run(tmp_path, policy, lexicon)
    monkeypatch.setenv("RAKSHA_SECRET", SECRET.decode())
    assert tool.main([str(out)]) == 0
    report = out / "report.json"
    report.write_text(report.read_text().replace('"complete"', '"failed"'))
    assert tool.main([str(out)]) == 1
    assert tool.main([str(Path(tmp_path / "missing"))]) == 2

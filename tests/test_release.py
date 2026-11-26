"""The release gate: self-scan, repair, structural checks, staging and failure handling."""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import sys

import pytest
from conftest import ROOT, write_tree

from wards import pipeline as pipeline_module
from wards import validate, write
from wards.audit import ADDRESS, IDENTIFIER, NAME, Auditor
from wards.model import EntityType as E
from wards.model import Mention
from wards.pipeline import Pipeline
from wards.redact import Redactor
from wards.security import SafeLog, Secret, Terminated
from wards.textview import TextView

KNOWN = [
    (E.PATIENT_NAME, "Wanrisha Kharbuli", 100),
    (E.PHONE, "9812345670", 82),
    (E.AADHAAR, "4821 7365 9012", 86),
    (E.MRN, "KMC-100417", 92),
    (E.EMAIL, "wanrisha.k@example.org", 95),
    (E.VILLAGE, "Nagla Chakmau", 75),
    (E.RELATIVE_NAME, "Brihaspati Nongrum", 82),
]


@pytest.fixture
def auditor(lexicon) -> Auditor:
    auditor = Auditor(lexicon)
    for etype, value, priority in KNOWN:
        auditor.remember(Mention(etype, 0, len(value), value, priority=priority))
    return auditor


def kinds(auditor, text):
    return [(f.kind, text[f.start:f.end]) for f in auditor.findings(text)]


@pytest.mark.parametrize("text, kind", [
    ("seen: Wanrisha Kharbuli today", NAME),
    ("seen: WANRISHA  KHARBULI", NAME),
    ("file Kharbuli_Wanrisha", NAME),
    ("Wan\u200brisha Kharbuli", NAME),
    ("Wānrishā Kharbulī", NAME),
    ("Wаnrisha Kharbuli", NAME),                    # Cyrillic a
    ("Wanrisha%20Kharbuli", NAME),
    ("&#87;anrisha Kharbuli", NAME),
    ("call 98123-45670", IDENTIFIER),
    ("call +91 98123 45670", IDENTIFIER),
    ("call ९८१२३४५६७०", IDENTIFIER),
    ("id 4821.7365.9012", IDENTIFIER),
    ("ref KMC/100417 ok", IDENTIFIER),
    ("ref kmc 100417", IDENTIFIER),
    ("mail Wanrisha.K@Example.org now", IDENTIFIER),
    ("lives at nagla chakmau.", ADDRESS),
])
def test_disguised_copies_are_found(auditor, text, kind):
    assert kind in [k for k, _ in kinds(auditor, text)]


@pytest.mark.parametrize("encode", [
    lambda s: base64.b64encode(s.encode()).decode(),
    lambda s: s.encode().hex(),
])
def test_encoded_copies_are_found(auditor, encode):
    blob = encode("callback for Wanrisha Kharbuli 9812345670")
    assert kinds(auditor, f"data {blob} end") == [(IDENTIFIER, blob)]


@pytest.mark.parametrize("text", [
    "Dr. Wanrisha Kharbuli reviewed the patient.",   # a doctor who shares the name
    "Platelet 150000-410000, WBC 100417 /uL",
    "PSN-7Q2K9D [PHONE] [ID] 229***",
    "Tab Amlodipine 5 mg 0-0-1 x 30 days",
    "Wanrisha was advised rest.",                    # one word is not a full name
    "Ward 1, Mainpuri",
])
def test_legitimate_text_is_not_flagged(auditor, text):
    assert kinds(auditor, text) == []


def test_weak_guesses_do_not_drive_the_scan(lexicon):
    auditor = Auditor(lexicon)
    auditor.remember(Mention(E.PATIENT_NAME, 0, 13, "Random Sample", priority=50))
    auditor.remember(Mention(E.LOCALITY, 0, 6, "Ward 4", priority=90))
    assert auditor.findings("Random Sample from Ward 4") == []


def test_digit_cores_matter_only_in_paths(auditor):
    assert auditor.is_clean("value 100417")
    assert not auditor.is_clean("export_100417", path=True)


def test_accents_are_folded_for_matching():
    assert TextView.of("José Müller Wān").text == "Jose Muller Wan"


# ------------------------------------------------------------------- end to end

FILES = {
    "a.csv": "UHID,Patient Name,Mobile,Visit Date,Notes\n"
             "KMC-100417,Wanrisha Kharbuli,9812345670,03/03/2026,stable\n"
             "KMC-100418,Temsula Ozukum,9012345678,04/03/2026,stable\n",
    "b.txt": "UHID: KMC-100417\nPatient Name: Wanrisha Kharbuli\nBP 120/80\n",
    "c.txt": "Hospital protocol. BP target below 130/80.\n",
}
SECRETS = ("Wanrisha", "Kharbuli", "9812345670", "100417")


def released(result) -> str:
    return result.everything()


def status(result, tmp_path, name) -> dict:
    return result.entry(tmp_path / "in0" / name)


def test_missed_redaction_is_repaired_by_the_self_scan(release, monkeypatch, tmp_path):
    real = Redactor.segment

    def forgetful(self, segment, context, forced=None):
        if segment.label == "Mobile":        # a bug that leaves one column untouched
            return
        real(self, segment, context, forced)

    monkeypatch.setattr(Redactor, "segment", forgetful)
    result = release(FILES)
    assert "9812345670" not in released(result)
    assert "[ID]" in result.text("a.csv")
    assert status(result, tmp_path, "a.csv")["status"] == "sanitized"
    assert re.search(r"repairs=([1-9]\d*)", result.log)


@pytest.mark.parametrize("corrupt, reason", [
    (lambda text: text.replace("BP 120/80", "BP 120/80 call 9812345670"),
     "self_scan_failed"),
    (lambda text: text + "extra line\n", "shape_changed"),
])
def test_a_writer_fault_quarantines_only_that_file(release, monkeypatch, tmp_path,
                                                   corrupt, reason):
    real = write.serialize

    def faulty(doc):
        return corrupt(real(doc)) if doc.rel == "b.txt" else real(doc)

    monkeypatch.setattr(write, "serialize", faulty)
    result = release(FILES)
    entry = status(result, tmp_path, "b.txt")
    assert entry["status"] == "quarantined" and entry["reason"] == reason
    assert not (result.root / "sanitized" / "b.txt").exists()
    assert status(result, tmp_path, "a.csv")["status"] == "sanitized"
    assert all(r["file"] != "sanitized/b.txt" for r in result.records)
    assert "9812345670" not in released(result)


def test_clean_documents_survive_the_gate_unchanged(release):
    assert release(FILES).text("c.txt") == FILES["c.txt"]


def test_nothing_staged_is_left_behind(release):
    result = release(FILES)
    assert sorted(p.name for p in result.root.iterdir()) == \
        ["logs", "records.jsonl", "report.json", "sanitized"]
    assert [p.name for p in (result.root / "logs").iterdir()] == ["run.jsonl"]
    assert result.report["status"] == "complete"


def test_a_stale_stage_is_cleared(tmp_path, policy, lexicon):
    source = write_tree(tmp_path / "in", FILES)
    out = write_tree(tmp_path / "out", {".stage/sanitized/old.txt": "Wanrisha Kharbuli"})
    Pipeline(policy, Secret(b"k"), SafeLog(io.StringIO()), lexicon).run(source, out)
    assert not (out / ".stage").exists()
    assert not (out / "sanitized" / "old.txt").exists()


def failed_run(tmp_path, policy, lexicon, error) -> tuple:
    source = write_tree(tmp_path / "in", FILES)
    out = tmp_path / "out"
    stream = io.StringIO()
    runner = Pipeline(policy, Secret(b"k"), SafeLog(stream), lexicon)
    with pytest.raises(type(error)):
        runner.run(source, out)
    return out, stream.getvalue()


def assert_nothing_released(out):
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["counts"]["files_sanitized"] == 0
    assert report["counts"]["files_quarantined"] == report["counts"]["files_total"] == 3
    assert list((out / "sanitized").iterdir()) == []
    assert not (out / ".stage").exists() and not (out / "records.jsonl").exists()
    text = (out / "report.json").read_text(encoding="utf-8")
    assert all(value not in text for value in SECRETS)


@pytest.mark.parametrize("error", [
    RuntimeError("Wanrisha Kharbuli"), MemoryError(), Terminated(), KeyboardInterrupt(),
])
def test_a_dataset_wide_failure_releases_nothing(tmp_path, policy, lexicon, monkeypatch,
                                                 error):
    def explode(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(pipeline_module.Linker, "link", explode)
    out, log = failed_run(tmp_path, policy, lexicon, error)
    assert_nothing_released(out)
    assert all(value not in log for value in SECRETS)


@pytest.mark.parametrize("failing_call", [1, 2, 3, 4])
def test_a_failure_while_publishing_releases_nothing(tmp_path, policy, lexicon,
                                                     monkeypatch, failing_call):
    from wards import release as release_module

    real, calls = release_module.os.replace, []

    def flaky(source, target):
        calls.append(target)
        if len(calls) == failing_call:
            raise OSError("disk")
        real(source, target)

    monkeypatch.setattr(release_module.os, "replace", flaky)
    out, _ = failed_run(tmp_path, policy, lexicon, OSError())
    assert_nothing_released(out)


def test_unverified_files_are_never_published(tmp_path):
    from wards.release import Release

    release = Release(tmp_path / "out")
    release.open()
    release.add("good.txt", "ok\n")
    release.add("stray.txt", "not declared\n")
    release.publish([], {"files": []}, ["good.txt"])
    assert [p.name for p in (tmp_path / "out" / "sanitized").iterdir()] == ["good.txt"]


def test_missing_staged_file_stops_publication(tmp_path):
    from wards.release import Release

    release = Release(tmp_path / "out")
    release.open()
    with pytest.raises(RuntimeError):
        release.publish([], {}, ["absent.txt"])
    release.abort()
    release.abort()                      # safe to repeat
    assert not (tmp_path / "out" / "report.json").exists()


def test_a_locator_that_names_someone_is_withheld(release):
    result = release({
        "a.txt": "Patient Name: Wanrisha Kharbuli\nUHID: KMC-100417\n",
        "b.json": json.dumps({"Wanrisha Kharbuli": [{"uhid": "KMC-100417"}]}),
    })
    assert "Wanrisha" not in released(result)


def test_core_dumps_are_disabled():
    code = ("import resource, sys; sys.path.insert(0, sys.argv[1]);"
            "from wards.security import harden_process; harden_process();"
            "print(resource.getrlimit(resource.RLIMIT_CORE)[0])")
    run = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT)], capture_output=True,
                         text=True, check=True)
    assert run.stdout.strip() == "0"


# ------------------------------------------------------------------------- FHIR


def bundle(**patient) -> dict:
    return {"resourceType": "Bundle", "entry": [
        {"fullUrl": "urn:uuid:1", "resource": {"resourceType": "Patient", "id": "p-1",
                                              **patient}},
        {"fullUrl": "urn:uuid:2", "resource": {"resourceType": "Encounter", "id": "e-1",
                                              "subject": {"reference": "urn:uuid:1"}}},
    ]}


def test_valid_bundle_has_no_faults():
    assert not validate.fhir_faults(bundle(birthDate="1981-07-09"))


@pytest.mark.parametrize("patient, fault", [
    ({"birthDate": "[DOB]"}, "bad_date.birthDate"),
    ({"id": "[ID]"}, "bad_resource_id"),
    ({"managingOrganization": {"reference": "urn:uuid:9"}}, "unresolved_reference"),
    ({"link": [{"other": {"reference": "Patient/zzz"}}]}, "unresolved_reference"),
    ({"gender": ""}, "empty_string"),
    ({"text": {"div": "<div>open"}}, "narrative_not_xml"),
    ({"text": {"div": "<!DOCTYPE x><div/>"}}, "narrative_declaration"),
    ({"photo": [{"contentType": "text/plain", "data": "@@@"}]}, "bad_attachment_data"),
])
def test_bundle_faults_are_named(patient, fault):
    assert validate.fhir_faults(bundle(**patient))[fault] >= 1


def test_inherited_faults_do_not_quarantine(release, tmp_path):
    result = release({"f.json": json.dumps(bundle(managingOrganization={
        "reference": "urn:uuid:9"}))})
    assert status(result, tmp_path, "f.json")["status"] == "sanitized"


def test_introduced_faults_quarantine_the_bundle(release, tmp_path, monkeypatch):
    real = write.serialize

    def breaking(doc):
        return real(doc).replace("urn:uuid:1", "urn:uuid:broken", 1)

    monkeypatch.setattr(write, "serialize", breaking)
    result = release({"f.json": json.dumps(bundle())})
    entry = status(result, tmp_path, "f.json")
    assert entry["status"] == "quarantined" and entry["reason"] == "fhir_faults_added"

"""End to end: the output contract, privacy guarantees, error handling and the CLI."""

from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, write_tree

from wards.errors import ConfigError
from wards.pipeline import Pipeline
from wards.security import SafeLog, Secret

PERSONAL = ("Wanrisha", "Kharbuli", "वानरिशा", "Brihaspati", "Temsula", "Ozukum",
            "9812345670", "9812 345670", "482173659012", "4821 7365 9012", "100417", "100500",
            "Chakmau", "205265")

REFERRAL = base64.b64encode(
    b"Referral: Wanrisha Kharbuli (KMC-100417), mobile 9812345670.").decode()

BUNDLE = {
    "resourceType": "Bundle", "type": "document", "id": "b1",
    "meta": {"source": "#KMC-100417"},
    "entry": [
        {"fullUrl": "urn:uuid:11111111-2222-3333-4444-555555555555", "resource": {
            "resourceType": "Patient", "id": "11111111-2222-3333-4444-555555555555",
            "text": {"status": "generated",
                     "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">Wanrisha Kharbuli, "
                            "44 yrs, female, KMC-100417</div>"},
            "identifier": [{"system": "urn:kmc:uhid", "value": "KMC-100417"}],
            "name": [{"text": "Wanrisha Kharbuli", "given": ["Wanrisha"], "family": "Kharbuli"}],
            "telecom": [{"system": "phone", "value": "+91-9812345670"}],
            "gender": "female", "birthDate": "1981-07-09",
            "address": [{"line": ["H.No. 7, Mohalla Purana Tola"], "city": "Chakmau",
                         "district": "Mainpuri", "postalCode": "205265"}],
            "contact": [{"name": {"text": "Brihaspati Kharbuli"}}]}},
        {"resource": {"resourceType": "Practitioner", "id": "p1",
                      "name": [{"text": "Dr. Anil Mehra"}]}},
        {"resource": {"resourceType": "Encounter", "id": "e1",
                      "period": {"start": "2026-03-03", "end": "2026-03-12"}}},
        {"resource": {"resourceType": "DocumentReference", "id": "d1", "content": [
            {"attachment": {"contentType": "text/plain", "data": REFERRAL}}]}},
    ],
}

FILES = {
    "reg/patients.csv":
        "UHID,Patient Name,Gender,Age,DOB,Relative Name,Village,District,PIN,Mobile,Aadhaar\n"
        "KMC-100417,Wanrisha Kharbuli,F,44,09/07/1981,Brihaspati Kharbuli,Chakmau,Mainpuri,"
        "205265,9812345670,482173659012\n",
    "opd/visits.json": json.dumps({
        "_meta": {"source_path": "D:\\exports\\Wanrisha_Kharbuli_100417\\visits.json"},
        "visits": [{"patient_ref": "KMC-100417", "visit_date": "03/03/2026",
                    "review_date": "17/03/2026", "doctor": "Dr. Anil Mehra",
                    "patient": {"name": "Wanrisha Kharbuli", "age": 44,
                                "mobile": 9812345670},
                    "vitals": {"bp": "120/80", "temp_f": 98.60},
                    "notes": "Wanrisha ji ka BP 120/80. Tab Amlodipine 5 mg 0-0-1."}]},
        indent=2),
    "fhir/bundle.json": json.dumps(BUNDLE),
    "notes/Wanrisha_Kharbuli_KMC-100417.txt":
        "रोगी का नाम: वानरिशा खरबुली\r\nUHID: KMC-100417\r\nमोबाइल: ९८१२३४५६७०\r\n"
        "भर्ती की तारीख: 03/03/2026\r\nछुट्टी की तारीख: 12/03/2026\r\n"
        "Diagnosis: Essential hypertension (I10). BP 150/90.\r\n",
    "misc/index.json": json.dumps({"by_name": {"Wanrisha Kharbuli": "KMC-100417",
                                               "Temsula Ozukum": "KMC-100500"}}),
    "misc/sheet.csv": "time,KMC-100417 Wanrisha,KMC-100500 Temsula\n06:00,120/80,130/85\n",
    "clean/formulary.csv": "drug,strength,form\nAmlodipine,5 mg,Tab\nORS,1 sachet,Sachet\n",
    "clean/protocol.txt": "Any patient with C/O fever needs a test. Dr. Anil Mehra is in charge.\n",
    "bad/scan.pdf": b"%PDF-1.4 Wanrisha Kharbuli 9812345670",
    "bad/cut.json": '{"patient": "Wanrisha Kharbuli", "mobile": "98123',
    "bad/empty.csv": b"",
    "bad/Wanrisha_photo.jpg": b"\xff\xd8\xff\xe0 Wanrisha",
}
QUARANTINED = {"bad/scan.pdf", "bad/cut.json", "bad/empty.csv", "bad/Wanrisha_photo.jpg"}
CLEAN = {"clean/formulary.csv", "clean/protocol.txt"}


@pytest.fixture
def full(release):
    return release(FILES)


def outputs(result, tmp_path) -> dict:
    source = tmp_path / "in0"
    return {rel: result.entry(source / rel) for rel in FILES}


def test_every_input_file_is_accounted_for(full):
    counts = full.report["counts"]
    assert counts["files_total"] == len(FILES) == len(full.report["files"])
    assert counts["files_quarantined"] == len(QUARANTINED)
    assert counts["files_sanitized"] == len(FILES) - len(QUARANTINED)
    exported = list((full.root / "sanitized").rglob("*"))
    assert sum(p.is_file() for p in exported) == counts["files_sanitized"]
    assert full.report["timing"]["seconds"] >= 0
    assert full.report["resources"]["peak_rss_mb"] > 0


def test_unsupported_files_are_never_copied(full, tmp_path):
    for rel, entry in outputs(full, tmp_path).items():
        if rel in QUARANTINED:
            assert entry["status"] == "quarantined" and entry["output"] is None
            assert entry["reason"]
        else:
            assert entry["status"] == "sanitized"
            assert (full.root / entry["output"]).is_file()


def test_no_personal_data_anywhere_in_the_release(full):
    haystack = full.everything()
    for path in (full.root / "sanitized").rglob("*.json"):
        for entry in json.loads(path.read_text(encoding="utf-8")).get("entry", []):
            for content in entry["resource"].get("content", []):
                haystack += base64.b64decode(content["attachment"]["data"]).decode()
    folded = haystack.casefold()
    leaked = [value for value in PERSONAL if value.casefold() in folded]
    assert leaked == []


def test_clean_documents_are_byte_identical(full):
    for rel in CLEAN:
        assert full.text(rel) == FILES[rel]


def test_pii_file_names_are_renamed_and_declared(full, tmp_path):
    entry = outputs(full, tmp_path)["notes/Wanrisha_Kharbuli_KMC-100417.txt"]
    assert entry["output"].startswith("sanitized/notes/file_")
    assert entry["output"].endswith(".txt")


def test_one_patient_one_pseudonym_everywhere(full, tmp_path):
    names = outputs(full, tmp_path)
    seen = {r["patient"] for r in full.records if r["file"] in {
        names[rel]["output"] for rel in ("reg/patients.csv", "opd/visits.json",
                                         "fhir/bundle.json",
                                         "notes/Wanrisha_Kharbuli_KMC-100417.txt")}}
    assert len(seen) == 1 and None not in seen
    for record in full.records:
        if record["patient"]:
            assert record["patient"] in (full.root / record["file"]).read_text("utf-8")


def test_dates_shift_together_and_keep_the_stay(full, tmp_path):
    names = outputs(full, tmp_path)
    visit = json.loads((full.root / names["opd/visits.json"]["output"]).read_text())["visits"][0]
    bundle = json.loads((full.root / names["fhir/bundle.json"]["output"]).read_text())
    period = next(e["resource"]["period"] for e in bundle["entry"]
                  if e["resource"]["resourceType"] == "Encounter")
    seen = dt.datetime.strptime(visit["visit_date"], "%d/%m/%Y").date()
    review = dt.datetime.strptime(visit["review_date"], "%d/%m/%Y").date()
    start = dt.date.fromisoformat(period["start"])
    assert (review - seen).days == 14
    assert (dt.date.fromisoformat(period["end"]) - start).days == 9
    assert seen == start != dt.date(2026, 3, 3)


def test_json_keeps_structure_types_and_numbers(full, tmp_path):
    names = outputs(full, tmp_path)
    text = (full.root / names["opd/visits.json"]["output"]).read_text()
    data = json.loads(text)
    visit = data["visits"][0]
    assert list(visit) == list(json.loads(FILES["opd/visits.json"])["visits"][0])
    assert visit["patient"]["mobile"] == 0
    assert visit["patient"]["age"] == "40-44"
    assert '"temp_f": 98.6' in text
    assert "Tab Amlodipine 5 mg 0-0-1" in visit["notes"]
    assert visit["doctor"] == "Dr. Anil Mehra"


def test_fhir_stays_well_formed(full, tmp_path):
    from xml.etree import ElementTree

    names = outputs(full, tmp_path)
    bundle = json.loads((full.root / names["fhir/bundle.json"]["output"]).read_text())
    patient = bundle["entry"][0]["resource"]
    assert patient["id"] == BUNDLE["entry"][0]["resource"]["id"]
    ElementTree.fromstring(patient["text"]["div"])
    assert "birthDate" not in patient
    assert patient["address"][0]["postalCode"] == "205***"
    assert patient["address"][0]["district"] == patient["address"][0]["city"] == "Mainpuri"
    assert bundle["entry"][1]["resource"]["name"][0]["text"] == "Dr. Anil Mehra"


def test_csv_keeps_shape(full, tmp_path):
    names = outputs(full, tmp_path)
    rows = list(csv.reader(io.StringIO(
        (full.root / names["reg/patients.csv"]["output"]).read_text(encoding="utf-8"))))
    original = list(csv.reader(io.StringIO(FILES["reg/patients.csv"])))
    assert rows[0] == original[0]
    assert [len(r) for r in rows] == [len(r) for r in original]


def test_text_keeps_line_endings(full, tmp_path):
    names = outputs(full, tmp_path)
    data = (full.root / names["notes/Wanrisha_Kharbuli_KMC-100417.txt"]["output"]).read_bytes()
    assert data.count(b"\r\n") == 6
    assert "Diagnosis: Essential hypertension (I10). BP 150/90." in data.decode("utf-8")


def test_headers_and_keys_stay_unique(full, tmp_path):
    names = outputs(full, tmp_path)
    header = (full.root / names["misc/sheet.csv"]["output"]).read_text().splitlines()[0]
    assert len(set(header.split(","))) == 3
    index = json.loads((full.root / names["misc/index.json"]["output"]).read_text())
    assert len(index["by_name"]) == 2


def test_same_secret_is_deterministic(release):
    def digest(result):
        h = hashlib.sha256()
        for path in sorted((result.root / "sanitized").rglob("*")):
            if path.is_file():
                h.update(path.relative_to(result.root).as_posix().encode() + path.read_bytes())
        h.update((result.root / "records.jsonl").read_bytes())
        return h.hexdigest()

    assert digest(release(FILES)) == digest(release(FILES))
    assert digest(release(FILES)) != digest(release(FILES, secret=b"another"))


def test_one_failing_file_does_not_stop_the_run(release, monkeypatch):
    from wards import documents

    real = documents.parse

    def explode(item, lexicon):
        if item.rel == "clean/protocol.txt":
            raise RuntimeError("Wanrisha 9812345670")
        return real(item, lexicon)

    monkeypatch.setattr(documents, "parse", explode)
    result = release(FILES)
    assert result.report["counts"]["files_quarantined"] == len(QUARANTINED) + 1
    assert "error=RuntimeError" in result.log
    assert "9812345670" not in result.log


def test_a_failed_write_leaves_no_partial_file(release, monkeypatch):
    from wards import write

    real = write.write_text_file

    def explode(path, content):
        real(path, content)
        if path.name == "formulary.csv":
            raise OSError("disk full")

    monkeypatch.setattr(write, "write_text_file", explode)
    result = release(FILES)
    assert not (result.root / "sanitized" / "clean" / "formulary.csv").exists()
    assert result.report["counts"]["files_sanitized"] == len(FILES) - len(QUARANTINED) - 1
    assert all(r["file"] != "sanitized/clean/formulary.csv" for r in result.records)


def test_binary_attachment_is_removed(release):
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n Wanrisha").decode()
    result = release({"a.json": json.dumps(
        {"attachment": {"contentType": "image/png", "data": payload}})})
    data = json.loads(result.text("a.json"))["attachment"]["data"]
    assert base64.b64decode(data) == b"[ATTACHMENT REMOVED]"


def test_birth_date_in_file_metadata_is_removed(release):
    result = release({"a.json": json.dumps({
        "meta": {"note": "DOB: 12/04/1981"},
        "rows": [{"uhid": "KMC-100417"}]})})
    assert "1981" not in result.text("a.json")


def test_old_patients_lose_their_birth_date(release):
    result = release({"a.csv": "UHID,Patient Name,Age,DOB\nKMC-1001,Asha Verma,95,12/04/1931\n"})
    row = result.text("a.csv").splitlines()[1]
    assert "90+" in row and "1931" not in row and "[DOB]" in row


def test_no_birth_year_survives_anywhere(release):
    result = release({
        "a.csv": "UHID,Patient Name,DOB,Visit Date\nKMC-1001,Asha Verma,12/04/1981,03/03/2026\n",
        "b.txt": "UHID: KMC-1001\nHistory recorded against 12/04/1981. Seen on 04/03/2026.\n",
        "c.json": json.dumps({"rows": [{"uhid": "KMC-1001", "dob": "1981-04-12"}]}),
    })
    released = "".join(result.text(name) for name in ("a.csv", "b.txt", "c.json"))
    assert "1981" not in released and "1980" not in released
    assert released.count("[DOB]") == 3


def test_birth_dates_can_still_be_shifted_by_policy(release):
    result = release({"a.csv": "UHID,Patient Name,DOB\nKMC-1001,Asha Verma,12/04/1981\n"},
                     dob_mode="shift")
    row = result.text("a.csv").splitlines()[1]
    assert "[DOB]" not in row and "12/04/1981" not in row


def test_newborn_visit_dates_keep_the_stay(release):
    result = release({"a.csv": "UHID,DOB,Admission Date,Discharge Date\n"
                               "KMC-1001,03/03/2026,03/03/2026,12/03/2026\n"})
    cells = result.text("a.csv").splitlines()[1].split(",")
    assert cells[1] == "[DOB]"
    first, last = (dt.datetime.strptime(c, "%d/%m/%Y") for c in cells[2:4])
    assert (last - first).days == 9


def test_numeric_dates_stay_numeric(release):
    result = release({"a.json": json.dumps(
        {"rows": [{"uhid": "KMC-100417", "visit_date": 20260301}]})})
    value = json.loads(result.text("a.json"))["rows"][0]["visit_date"]
    assert isinstance(value, int) and value != 20260301 and len(str(value)) == 8


def test_empty_input_folder(release):
    result = release({})
    assert result.report["counts"]["files_total"] == 0 and result.records == []


def test_input_is_never_modified(release, tmp_path):
    release(FILES)
    for rel, content in FILES.items():
        expected = content if isinstance(content, bytes) else content.encode("utf-8")
        assert (tmp_path / "in0" / rel).read_bytes() == expected


@pytest.mark.parametrize("layout, code", [
    ("missing", "input_is_not_a_directory"),
    ("same", "input_and_output_overlap"),
    ("inside", "input_and_output_overlap"),
    ("parent", "input_and_output_overlap"),
    ("stale", "output_already_has_results"),
])
def test_unsafe_paths_are_refused(tmp_path, policy, lexicon, layout, code):
    source = write_tree(tmp_path / "data" / "in", {"a.txt": "hello"})
    out = tmp_path / "out"
    if layout == "missing":
        source = tmp_path / "absent"
    elif layout == "same":
        out = source
    elif layout == "inside":
        out = source / "out"
    elif layout == "parent":
        out = tmp_path / "data"
    elif layout == "stale":
        write_tree(out, {"sanitized/old.txt": "stale"})
    pipeline = Pipeline(policy, Secret(b"k"), SafeLog(io.StringIO()), lexicon)
    with pytest.raises(ConfigError, match=code):
        pipeline.run(source, out)
    assert not (tmp_path / "out" / "report.json").exists()


# ------------------------------------------------------------------------ CLI


def cli(tmp_path, *args, secret="cli-secret-value"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RAKSHA_")}
    if secret is not None:
        env["RAKSHA_SECRET"] = secret
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *args], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)


def test_cli_release_prints_counts_only(tmp_path):
    source = write_tree(tmp_path / "in", FILES)
    result = cli(tmp_path, "--input", str(source), "--output", str(tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    printed = result.stdout + result.stderr
    assert all(value not in printed for value in PERSONAL)
    released = b"".join(p.read_bytes() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert b"cli-secret-value" not in released and "cli-secret-value" not in printed


@pytest.mark.parametrize("args, secret, code", [
    ((), "s", "input_and_output_required"),
    (("--bogus", "Wanrisha"), "s", "invalid_arguments"),
    (("--input", "in", "--output", "out"), None, "raksha_secret_not_set"),
    (("--input", "nowhere", "--output", "out"), "s", "input_is_not_a_directory"),
])
def test_cli_refuses_to_start(tmp_path, args, secret, code):
    (tmp_path / "in").mkdir()
    result = cli(tmp_path, *args, secret=secret)
    assert result.returncode == 2
    assert code in result.stderr and "Wanrisha" not in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out" / "report.json").exists()

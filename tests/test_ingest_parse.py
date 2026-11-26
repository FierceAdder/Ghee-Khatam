"""Format sniffing, decoding, quarantine decisions, parsing and column typing."""

from __future__ import annotations

import pytest

from wards import documents, ingest, write
from wards.columns import classify_json_key, classify_label, type_columns
from wards.errors import QuarantineError
from wards.model import Channel, DocKind
from wards.model import FieldType as F


def read(tmp_path, name: str, content: bytes, policy) -> ingest.InputFile:
    path = tmp_path / name
    path.write_bytes(content)
    return ingest.read_file(path, tmp_path, policy)


@pytest.mark.parametrize("name, content, reason", [
    ("a.csv", b"", "empty_file"),
    ("a.txt", b"  \n\t\n", "empty_file"),
    ("a.pdf", b"%PDF-1.4 text", "unsupported_format"),
    ("a.jpg", b"\xff\xd8\xff\xe0 data", "unsupported_format"),
    ("renamed.txt", b"PK\x03\x04zip", "unsupported_format"),
    ("a.xml", b"<?xml version='1.0'?><a/>", "unsupported_format"),
    ("hidden.txt", b"<ClinicalDocument><x/></ClinicalDocument>", "unsupported_format"),
    ("a.jsonl", b'{"a":1}\n{"a":2}\n', "unsupported_format"),
    ("a.docx", b"plain words", "unsupported_format"),
    ("a.json", b'{"visits": [{"patient": "x', "malformed_json"),
    ("a.json", b"[" * 2000 + b"]" * 2000, "nesting_too_deep"),
    ("a.txt", b"note \xff\xfe\xc3( end", "undecodable_text"),
    ("a.txt", b"\xd5\xe2\xc4\xca\xc9 legacy", "undecodable_text"),
    ("a.txt", b"abc\x00\x01\x02" * 40, "binary_content"),
    ("a.txt", b"bell \x07 char", "binary_content"),
])
def test_unsafe_files_are_quarantined(tmp_path, policy, name, content, reason):
    item = read(tmp_path, name, content, policy)
    assert item.quarantined and item.reason == reason
    assert item.text == ""


@pytest.mark.parametrize("encoding, expected", [
    ("utf-8", "utf-8"), ("utf-8-sig", "utf-8-sig"), ("utf-16", "utf-16"),
    ("utf-16-le", "utf-16-le"), ("utf-16-be", "utf-16-be"),
])
def test_encodings(tmp_path, policy, encoding, expected):
    item = read(tmp_path, "a.csv", "Name,Note\nAsha,ok\n".encode(encoding), policy)
    assert not item.quarantined and item.encoding == expected
    assert item.text.startswith("Name")


def test_legacy_cp1252(tmp_path, policy):
    item = read(tmp_path, "a.csv", "Name,Note\nx,101°F – ok\n".encode("cp1252"), policy)
    assert item.encoding == "cp1252" and "101°F" in item.text


def test_json_behind_a_csv_extension(tmp_path, policy):
    item = read(tmp_path, "a.csv", b'{"patient": "x"}', policy)
    assert item.kind is DocKind.JSON


def test_chat_export_starting_with_a_bracket_is_text(tmp_path, policy):
    item = read(tmp_path, "a.txt", b"[30/05/26, 9:32 pm] X: hello", policy)
    assert item.kind is DocKind.TEXT


@pytest.mark.parametrize("header, delimiter", [
    ("a,b,c", ","), ("a;b;c", ";"), ("a\tb\tc", "\t"), ("a|b|c", "|"), ('"a,x";b', ";"),
])
def test_delimiter_sniffing(header, delimiter):
    assert ingest.sniff_delimiter(header + "\n" + header + "\n", ".csv") == delimiter


def test_size_limit(tmp_path, policy):
    from dataclasses import replace

    item = read(tmp_path, "a.txt", b"x" * (2 * 1024 * 1024), replace(policy, max_file_mb=1))
    assert item.reason == "file_too_large"


def test_symlinks_are_not_followed(tmp_path, policy):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    source = tmp_path / "in"
    source.mkdir()
    (source / "link.txt").symlink_to(outside)
    (source / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    listed = ingest.list_files(source)
    assert [p.name for p in listed] == ["dirlink", "link.txt"]
    assert all(ingest.read_file(p, source, policy).reason == "not_a_regular_file"
               for p in listed)


# --------------------------------------------------------------------- columns


@pytest.mark.parametrize("label, kind", [
    ("Patient's Name", F.PATIENT_NAME), ("PatientName", F.PATIENT_NAME),
    ("Naam", F.PATIENT_NAME), ("मरीज़ का नाम", F.PATIENT_NAME),
    ("Pita/Pati ka Naam", F.RELATIVE_NAME), ("Guardian's Name", F.RELATIVE_NAME),
    ("Contact#", F.PHONE), ("Mobile No", F.PHONE), ("callback", F.PHONE),
    ("Gram/Mohalla", F.VILLAGE), ("Jila", F.DISTRICT), ("Zip", F.PIN),
    ("Janm Tithi", F.DOB), ("DOB (MM/DD/YYYY)", F.DOB), ("Reg Date", F.DATE),
    ("Reg No", F.MRN), ("hospital_uhid", F.MRN), ("patient_ref", F.MRN),
    ("Years", F.AGE), ("Umar", F.AGE), ("age_sex", F.AGE_SEX), ("Ling", F.SEX),
    ("aadhar_no", F.AADHAAR), ("abha_address", F.ABHA_ADDRESS), ("ABHA No", F.ABHA_NUMBER),
    ("payer_pan", F.PAN), ("pmjay_id", F.INSURANCE_ID), ("ref_doctor", F.DOCTOR_NAME),
    ("LastName", F.LAST_NAME), ("Hospital Name", F.ORG), ("Drug Name", F.ORG),
    ("visit_id", F.CODE), ("loinc", F.CODE), ("Remarks", F.TEXT), ("result", F.CLINICAL),
])
def test_header_keywords(label, kind):
    assert classify_label(label) is kind


def test_unknown_columns_are_typed_by_their_values(lexicon):
    header = ["c1", "c2", "c3", "c4"]
    rows = [["Asha Verma", "9812345670", "4821 7365 9012", "12/04/1977"],
            ["Naveen Bajpai", "9012345678", "7391 4628 5510", "01/05/1980"],
            ["Kusum Devi", "8123456709", "5555 6666 7777", "11/11/1991"]]
    assert type_columns(header, rows, lexicon) == [F.PATIENT_NAME, F.PHONE, F.AADHAAR, F.DATE]


def test_values_override_a_misleading_header(lexicon):
    rows = [["9812345670"], ["9012345678"], ["8123456709"]]
    assert type_columns(["Patient Name"], rows, lexicon) == [F.PHONE]


@pytest.mark.parametrize("path, kind", [
    (("patient", "name"), F.PATIENT_NAME),
    (("patients", "name", "first"), F.FIRST_NAME),
    (("patients", "guardian", "name"), F.RELATIVE_NAME),
    (("facility", "name"), F.ORG), (("panels", "name"), F.ORG),
    (("tickets", "meta", "raised_by", "name"), F.PERSON_NAME),
    (("data", "pt", "fullName"), F.PATIENT_NAME), (("name",), F.NAME),
    (("patients", "address", "line"), F.ADDRESS), (("patients", "address", "city"), F.VILLAGE),
    (("visits", "doctor"), F.DOCTOR_NAME), (("_export_meta", "source_path"), F.PATH),
    (("visits", "rx", "drug"), F.CLINICAL),
])
def test_json_key_typing(path, kind):
    assert classify_json_key(path) is kind


# ---------------------------------------------------------------------- parsing


def parse(tmp_path, name: str, content: str, policy, lexicon):
    return documents.parse(read(tmp_path, name, content.encode("utf-8"), policy), lexicon)


def test_csv_locators_and_headers(tmp_path, policy, lexicon):
    doc = parse(tmp_path, "a.csv", 'Name,Note\nA,"two\nlines"\n\nB,x,extra\n', policy, lexicon)
    assert [r.loc for r in doc.records] == ["row:0", "row:1", "row:2"]
    assert all(s.channel is Channel.HEADER for s in doc.file_segments())


@pytest.mark.parametrize("content, locs", [
    ('{"visits":[{"patient_ref":"A/1"},{"patient_ref":"A/2"}]}', ["/visits/0", "/visits/1"]),
    ('{"data":{"rows":[{"name":"Asha Verma"}]}}', ["/data/rows/0"]),
    ('[{"mobile":"9812345670"}]', ["/0"]),
    # one patient whose results are a list: a single record
    ('{"patient":{"name":"x"},"panels":[{"name":"Routine"}]}', ["doc"]),
    ('{"wards":[{"ward":"Ward 1","beds":3}]}', ["doc"]),
    ('{"a/b":[{"uhid":"X-1"}]}', ["/a~1b/0"]),
])
def test_json_record_boundaries(tmp_path, policy, lexicon, content, locs):
    doc = parse(tmp_path, "a.json", content, policy, lexicon)
    assert [r.loc for r in doc.records] == locs


@pytest.mark.parametrize("name, content", [
    ("a.csv", 'h1;h2\r\n"a;b";c\r\n\r\nd\r\n'),
    ("a.tsv", "h1\th2\nx\ty\n"),
    ("a.txt", "line one\r\nline two\r\n"),
    ("a.json", '{\n  "a": 0.80,\n  "b": [\n    "x"\n  ]\n}\n'),
    ("a.json", '{"a":[{"b":null}]}'),
])
def test_untouched_content_is_written_back_unchanged(tmp_path, policy, lexicon, name, content):
    assert write.serialize(parse(tmp_path, name, content, policy, lexicon)) == content


def test_attachments_are_decoded(tmp_path, policy, lexicon):
    import base64

    payload = base64.b64encode(b"Referral for a patient").decode()
    doc = parse(tmp_path, "a.json",
                f'{{"attachment":{{"contentType":"text/plain","data":"{payload}"}}}}',
                policy, lexicon)
    segment = next(s for s in doc.segments if s.codec)
    assert segment.text == "Referral for a patient"
    assert payload in write.serialize(doc)


def test_binary_attachments_cannot_be_inspected(tmp_path, policy, lexicon):
    doc = parse(tmp_path, "a.json",
                '{"attachment":{"contentType":"image/png","data":"iVBORw0KGgo="}}',
                policy, lexicon)
    assert any(s.field is F.ATTACHMENT for s in doc.segments)


def test_unparsed_input_is_rejected(lexicon):
    with pytest.raises(QuarantineError):
        documents.parse(ingest.InputFile(rel="x", sha256="0"), lexicon)

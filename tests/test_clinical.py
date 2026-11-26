"""Suppression of diagnoses held by too few patients."""

from __future__ import annotations

import json

PEOPLE = [("KMC-100417", "Wanrisha Kharbuli"), ("KMC-100418", "Temsula Ozukum"),
          ("KMC-100419", "Zuchamo Lotha")]


def visit(uhid, code, display, note=""):
    return {"patient_ref": uhid, "diagnosis": [{"code": code, "display": display}],
            "notes": note, "vitals": {"bp": "120/80"}, "history": "K/C/O DM2 x 8 yrs",
            "rx": [{"drug": "Amlodipine", "strength": "5 mg", "frequency": "1-0-1"}]}


COMMON = ("A01.0", "Typhoid fever")
RARE = ("B55.0", "Visceral leishmaniasis (kala-azar)")


def files():
    visits = [visit(uhid, *COMMON) for uhid, _ in PEOPLE]
    visits.append(visit("KMC-100417", *RARE, note="K/C/O kala-azar, review"))
    return {
        "opd.json": json.dumps({"visits": visits}),
        "ds.txt": "UHID: KMC-100417\nDiagnosis: काला-अज़ार (B55.0); Typhoid fever (A01.0)\n",
        "note.txt": "UHID: KMC-100417\nKnown visceral leishmaniasis, Kala-Azar treated.\n",
        "protocol.txt": "Kala-azar (B55.0) protocol for health workers.\n",
    }


def test_a_diagnosis_of_one_patient_is_removed_everywhere(release):
    result = release(files())
    text = result.text("opd.json") + result.text("ds.txt") + result.text("note.txt")
    folded = text.casefold()
    for spelling in ("b55.0", "leishmaniasis", "kala-azar", "काला"):
        assert spelling not in folded, spelling
    assert "R69" in text and "[CONDITION]" in text


def test_common_diagnoses_and_other_medical_values_survive(release):
    result = release(files())
    visits = json.loads(result.text("opd.json"))["visits"]
    assert [v["diagnosis"][0] for v in visits[:3]] == [
        {"code": "A01.0", "display": "Typhoid fever"}] * 3
    assert visits[3]["rx"][0] == {"drug": "Amlodipine", "strength": "5 mg",
                                  "frequency": "1-0-1"}
    assert visits[3]["vitals"] == {"bp": "120/80"}
    assert "Typhoid fever (A01.0)" in result.text("ds.txt")


def test_documents_about_nobody_keep_their_content(release):
    result = release(files())
    assert result.text("protocol.txt") == files()["protocol.txt"]


def test_suppression_can_be_turned_off(release):
    result = release(files(), rare_diagnosis_patients=0)
    assert "B55.0" in result.text("opd.json")


def test_threshold_counts_patients_not_records(release):
    records = [visit("KMC-100417", *RARE) for _ in range(5)] \
        + [visit(uhid, *COMMON) for uhid, _ in PEOPLE]
    result = release({"opd.json": json.dumps({"visits": records})})
    assert "B55.0" not in result.text("opd.json")


def test_fhir_stays_valid_after_suppression(release, tmp_path):
    bundle = {"resourceType": "Bundle", "entry": [
        {"fullUrl": "urn:uuid:1", "resource": {
            "resourceType": "Patient", "id": "p1",
            "identifier": [{"system": "urn:x:uhid", "value": "KMC-100417"}]}},
        {"fullUrl": "urn:uuid:2", "resource": {
            "resourceType": "Condition", "id": "c1", "subject": {"reference": "urn:uuid:1"},
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10",
                                 "code": RARE[0], "display": RARE[1]}], "text": RARE[1]}}},
    ]}
    result = release({"b.json": json.dumps(bundle)})
    out = json.loads(result.text("b.json"))
    code = out["entry"][1]["resource"]["code"]
    assert code["coding"][0]["code"] == "R69" and code["text"] == "[CONDITION]"
    assert code["coding"][0]["system"] == "http://hl7.org/fhir/sid/icd-10"


def test_aliases():
    from wards.clinical import aliases

    assert aliases("Dengue, unspecified") >= {"dengue, unspecified", "dengue"}
    assert aliases(RARE[1]) >= {"kala-azar", "visceral leishmaniasis"}
    assert aliases("Unspecified malaria") >= {"malaria"}
    assert aliases("TB") == set()

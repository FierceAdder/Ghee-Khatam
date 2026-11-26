"""Input worded in ways the vocabulary does not know.

Fields are typed by what their values look like, cues work without name lists, and no
fragment survives an awkward overlap. All names and numbers here are invented.
"""

from __future__ import annotations

import json

import pytest

from wards import documents, ingest
from wards.columns import Context, infer_from_values, type_columns
from wards.detect import Detector
from wards.detect.engine import resolve
from wards.knowledge import Knowledge
from wards.link import record_names
from wards.model import CLIPPED, Mention, Record, Role, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.textview import TextView

PEOPLE = ["Tashi Wangdi", "Meren Longkumer", "Sunita Kerketta", "Birsa Oraon"]
IDS = Context(identifiers=True)
PLACES = Context(places=True)


# ------------------------------------------------------------- typing by values


@pytest.mark.parametrize("values, context, kind", [
    (["QXR/26/004411", "QXR/26/004412"], Context(), F.MRN),
    (["226010", "229 402"], PLACES, F.PIN),
    (["Devipur", "Rampurwa", "Sohanganj"], Context(), F.VILLAGE),
    (["Dr. A. Kujur", "Dr. M. Longkumer"], IDS, F.DOCTOR_NAME),
    (["14-Mar-1961", "02-Jan-1994"], Context(), F.DOB),
    (["14-Mar-2026", "02-Apr-2026"], Context(), F.DATE),
    (PEOPLE, IDS, F.PERSON_NAME),
])
def test_values_decide_the_type(lexicon, values, context, kind):
    assert infer_from_values(values, lexicon, context) is kind


@pytest.mark.parametrize("values, context", [
    (["226010", "229402"], Context()),                    # no places: maybe a count
    (PEOPLE, Context()),                                  # nothing says these are patients
    (["Blood Sugar", "Blood Sugar"] * 10, IDS),           # repeats like a category
    (["City Care Hospital", "Hill View Clinic"], IDS),
    (["Lucknow", "Kanpur"], PLACES),                      # districts are not villages
])
def test_values_that_prove_nothing_stay_untyped(bare_lexicon, values, context):
    assert infer_from_values(values, bare_lexicon, context) is None


@pytest.mark.parametrize("value, context, kind", [
    ("482173659012", Context(), F.AADHAAR),
    ("QXR/26/004411", Context(), F.MRN),
    ("Devipur", PLACES, F.VILLAGE),
    ("Devipur", Context(), None),
    ("Meren Longkumer", IDS, None),                       # one name is not a pattern
])
def test_a_single_value_counts_only_when_its_shape_is_telling(lexicon, value, context, kind):
    assert infer_from_values([value], lexicon, context) is kind


def test_a_table_with_unfamiliar_headers(lexicon):
    header = ["h1", "h2", "h3", "h4", "h5"]
    rows = [[name, f"QXR/26/00{i}411", village, pin, born]
            for i, (name, village, pin, born) in enumerate(zip(
                PEOPLE, ["Devipur", "Rampurwa", "Sohanganj", "Devipur"],
                ["226010", "229402", "226012", "226010"],
                ["14/03/1961", "02/01/1994", "19/07/2011", "30/11/1980"], strict=True))]
    assert type_columns(header, rows, lexicon) == [
        F.PATIENT_NAME, F.MRN, F.VILLAGE, F.PIN, F.DOB]


def test_two_one_word_name_columns_are_one_name_in_parts(lexicon):
    rows = [["Longkumer", "Meren"], ["Kerketta", "Sunita"], ["Oraon", "Birsa"]]
    assert sorted(type_columns(["Name2", "Name1"], rows, lexicon), key=str) \
        == sorted([F.FIRST_NAME, F.LAST_NAME], key=str)


def test_an_unrecognised_partner_of_a_name_part_is_the_other_part(lexicon):
    rows = [["Longkumer", "Meren"], ["Kerketta", "Sunita"], ["Oraon", "Birsa"]]
    assert type_columns(["Second Name", "First Name"], rows, lexicon) \
        == [F.LAST_NAME, F.FIRST_NAME]


def test_a_name_written_in_two_scripts_is_not_split(lexicon):
    rows = [["Sunita", "सुनीता"], ["Birsa", "बिरसा"]]
    assert F.LAST_NAME not in type_columns(["Name1", "Name2"], rows, lexicon)


# --------------------------------------------------------- unfamiliar JSON keys


def parse(tmp_path, policy, lexicon, tree):
    path = tmp_path / "data.json"
    path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    return documents.parse(ingest.read_file(path, tmp_path, policy), lexicon)


def test_unfamiliar_keys_split_a_list_into_records(tmp_path, policy, lexicon):
    rows = [{"k1": name, "k2": f"QXR/26/00{i}411", "test": "Serum Creatinine"}
            for i, name in enumerate(PEOPLE)]
    doc = parse(tmp_path, policy, lexicon, {"rows": rows})
    assert len(doc.records) == len(PEOPLE)
    fields = {s.label: s.field for s in doc.records[0].segments}
    assert fields["k1"] is F.PATIENT_NAME and fields["k2"] is F.MRN


def test_a_known_patient_key_keeps_other_names_unclaimed(tmp_path, policy, lexicon):
    rows = [{"patient_name": name, "mobile": "9812345670", "k9": other}
            for name, other in zip(PEOPLE, reversed(PEOPLE), strict=True)]
    doc = parse(tmp_path, policy, lexicon, rows)
    fields = {s.label: s.field for s in doc.records[0].segments}
    assert fields["k9"] is F.NAME


def test_unfamiliar_keys_are_redacted_end_to_end(release):
    rows = [{"k1": name, "k2": f"QXR/26/00{i}411", "k3": "98123456%02d" % i,
             "diagnosis": "Dengue fever"} for i, name in enumerate(PEOPLE)]
    out = release({"export.json": json.dumps({"rows": rows})})
    text = out.text("export.json")
    for row in rows:
        assert row["k1"] not in text and row["k2"] not in text and row["k3"] not in text
    assert text.count("Dengue fever") == len(rows)


def test_a_number_labelled_as_insurance_still_links_to_the_same_hospital_number(release):
    out = release({
        "a.csv": "Card No,Patient Name,Mobile\nQXR/26/004411,Meren Longkumer,9812345670\n",
        "b.csv": "UHID,Patient Name,Ward\nQXR/26/004411,Meren Longkumer,4\n"})
    claims = out.claims()
    assert claims[("a.csv", "row:0")] == claims[("b.csv", "row:0")] is not None


# ---------------------------------------------------------- cues without lists


@pytest.fixture(scope="module")
def unlisted(bare_lexicon) -> Detector:
    return Detector(bare_lexicon)


def mentions(detector, text, *types):
    return [m.value for m in detector.detect_segment(Segment(text, field=F.TEXT))
            if m.etype in types]


NAMES = (E.PATIENT_NAME, E.RELATIVE_NAME)


@pytest.mark.parametrize("text, name", [
    ("ward of Meren Longkumer", "Meren Longkumer"),
    ("spouse Meren Longkumer", "Meren Longkumer"),
    ("child of: Meren Longkumer", "Meren Longkumer"),
    ("Care giver Meren Longkumer", "Meren Longkumer"),
    ("नाम: तेमजेन   आयु: 26 वर्ष", "तेमजेन"),
    ("तेमजेन   आयु: 26 वर्ष", "तेमजेन"),
    ("Meren Longkumer    Yrs/Sex: 44/F", "Meren Longkumer"),
    ("Meren akka came for review", "Meren"),
])
def test_cues_find_unlisted_names(unlisted, text, name):
    assert any(name in value for value in mentions(unlisted, text, *NAMES))


def test_the_person_before_a_spouse_is_the_patient(unlisted):
    found = unlisted.detect_segment(Segment("Sunita, spouse Birsa Oraon", field=F.TEXT))
    roles = {m.value: m.role for m in found if m.etype in NAMES}
    assert roles == {"Sunita": Role.PATIENT, "Birsa Oraon": Role.RELATIVE}


@pytest.mark.parametrize("text, etype, value", [
    ("घर पुराने शिव मंदिर के पास है", E.LANDMARK, "शिव मंदिर के पास"),
    ("रहने वाले सोहन खुर्द", E.VILLAGE, "सोहन खुर्द"),
    ("came from Sohan Kalan today", E.VILLAGE, "Sohan Kalan"),
    ("C/O Meren Longkumer, Ward 8", E.LOCALITY, "Ward 8"),
    ("बस्ती देवीपुर", E.VILLAGE, "देवीपुर"),
])
def test_places_in_unfamiliar_wording(unlisted, text, etype, value):
    assert any(value in found for found in mentions(unlisted, text, etype))


@pytest.mark.parametrize("label", ["DOB:", "D.O.B.:", "d/o/b", "Born on"])
def test_birth_date_labels_in_prose(detector, label):
    assert mentions(detector, f"{label} 14/03/1961", E.DOB) == ["14/03/1961"]


# ------------------------------------------------- medical words that are names


def test_a_medical_word_before_a_surname_is_part_of_the_name(detector):
    assert "Parkinson Verma" in mentions(detector, "Patient Name: Parkinson Verma", *NAMES)


@pytest.mark.parametrize("text", [
    "Patient: Parkinson's disease follow up",
    "known case of Parkinson disease",
    "Patient Insulin dose reviewed",
])
def test_conditions_and_medicines_are_not_names(detector, text):
    found = " ".join(mentions(detector, text, *NAMES))
    assert "Parkinson" not in found and "Insulin" not in found


def test_a_name_field_keeps_a_medical_first_name(detector):
    found = detector.detect_segment(Segment("Parkinson Verma", field=F.PATIENT_NAME))
    assert [m.value for m in found] == ["Parkinson Verma"]


# --------------------------------------------------------- overlaps and offsets


def span(etype, text, value, priority):
    start = text.index(value)
    return Mention(etype, start, start + len(value), value, priority=priority)


def test_the_uncovered_part_of_a_weaker_detection_survives():
    text = "ref 4821 7365 9012 345"
    strong = span(E.AADHAAR, text, "4821 7365 9012", 90)
    weak = span(E.OTHER_ID, text, "9012 345", 50)
    result = resolve([strong, weak], text)
    assert [(m.value, m.source) for m in result][1] == ("345", CLIPPED)


@pytest.mark.parametrize("etype, weaker", [
    (E.DATE, "12/04/2026 9"),           # a date cannot be rewritten in part
    (E.OTHER_ID, "2026 9"),             # a single stray digit is not an identifier
])
def test_some_leftovers_are_dropped(etype, weaker):
    text = "seen 12/04/2026 9 ok"
    result = resolve([span(E.DATE, text, "12/04/2026", 90), span(etype, text, weaker, 50)],
                     text)
    assert len(result) == 1


def test_fragments_teach_nothing_and_link_nothing(lexicon):
    fragment = Mention(E.MRN, 0, 9, "QXR/26/00", priority=50, source=CLIPPED)
    name = Mention(E.PATIENT_NAME, 0, 5, "Meren", role=Role.PATIENT, priority=90,
                   source=CLIPPED)
    knowledge = Knowledge(lexicon)
    knowledge._learn_mention(fragment)
    assert not knowledge.id_values and not knowledge.shapes
    segment = Segment("QXR/26/00", field=F.TEXT, mentions=[name])
    assert record_names(Record("k", "doc", [segment]), lexicon) == []


@pytest.mark.parametrize("source, cut", [
    ("सीता", "सीत"),          # stops before the final vowel sign
    ("राम कुमारी", "राम कुमार"),
])
def test_edits_cover_whole_characters(source, cut):
    view = TextView.of(source)
    low, high = view.source_span(0, len(cut))
    assert high == len(source) or source[high] == " "


# --------------------------------------------------------------------- staff
# Staff other than doctors are removed as [STAFF], never treated as the patient and never
# used for linking. Doctors and role words ("ASHA worker") stay.


@pytest.mark.parametrize("text, name", [
    ("Collected by: Meena Kujur (Lab Tech)", "Meena Kujur"),
    ("Sample collected by Meena Kujur", "Meena Kujur"),
    ("Staff Nurse Rekha Toppo on duty", "Rekha Toppo"),
    ("Sister Rekha gave injection", "Rekha"),
    ("SN Rekha Toppo", "Rekha Toppo"),
    ("ANM Sunita Lakra visited", "Sunita Lakra"),
    ("ASHA worker Sunita Lakra referred", "Sunita Lakra"),
    ("Verified by: Anil Minz, Lab Technician", "Anil Minz"),
    ("Anil Minz, Pharmacist", "Anil Minz"),
    ("नर्स रेखा टोप्पो ने इंजेक्शन दिया", "रेखा टोप्पो"),
])
def test_staff_names_are_found_as_staff(unlisted, text, name):
    found = unlisted.detect_segment(Segment(text, field=F.TEXT))
    assert [(m.value, m.role) for m in found if m.etype in NAMES] == [(name, Role.STAFF)]


@pytest.mark.parametrize("text", ["Dr. Anil Minz reviewed", "Referred by ASHA worker of the village",
                                  "Staff nurse informed the doctor"])
def test_doctors_and_role_words_stay(detector, text):
    assert mentions(detector, text, *NAMES) == []


@pytest.mark.parametrize("text", ["his sister Meena accompanied", "Name: ASHA TIRKEY  Age: 48/F"])
def test_relatives_and_patients_are_not_staff(detector, text):
    found = detector.detect_segment(Segment(text, field=F.TEXT))
    assert all(m.role is not Role.STAFF for m in found)


@pytest.mark.parametrize("path", [("lab_technician",), ("nurse",), ("verified_by",),
                                  ("collected_by",), ("staff_nurse", "name")])
def test_staff_keys(path):
    from wards.columns import classify_json_key

    assert classify_json_key(path) is F.STAFF_NAME


def test_staff_are_removed_and_do_not_disturb_linking(release):
    out = release({
        "reg.csv": "UHID,Patient Name,Mobile\nQXR/26/004411,Meren Longkumer,9812345670\n",
        "note.txt": "UHID QXR/26/004411. Patient Meren Longkumer has fever.\n"
                    "Staff Nurse Rekha Toppo gave paracetamol. Dr. Anil Minz reviewed.\n",
        "lab.json": '{"patient": {"name": "Meren Longkumer", "uhid": "QXR/26/004411"}, '
                    '"lab_technician": "Rekha Toppo", "test": "Haemoglobin", "value": 11.2}'})
    note, lab = out.text("note.txt"), out.text("lab.json")
    assert "Rekha" not in note + lab and "[STAFF]" in note and "[STAFF]" in lab
    assert "Dr. Anil Minz reviewed" in note
    claims = out.claims()
    assert claims[("note.txt", "doc")] == claims[("reg.csv", "row:0")] \
        == claims[("lab.json", "doc")] is not None

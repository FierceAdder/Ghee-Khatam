"""Fixes for defects an outside set showed, each reproduced with invented data."""

from __future__ import annotations

import json
import re

import pytest

from wards.columns import classify_json_key
from wards.model import FieldType as F

PSN = re.compile(r"^PSN-[0-9A-HJKMNP-TV-Z]{6}$")


def table(header: list[str], rows: list[list[str]]) -> str:
    return "".join(",".join(line) + "\n" for line in [header, *rows])


def released(out) -> str:
    folder = out.root / "sanitized"
    return "\n".join(f"{p.relative_to(folder)}\n{p.read_text(encoding='utf-8')}"
                     for p in sorted(folder.rglob("*")) if p.is_file())


# ------------------------------------------------------ separators inside numbers (G1)

PEOPLE = ["Lalrinzuali Hmar", "Pushpa Toppo", "Dechen Bhutia", "Kiran Mahato"]
SERIALS = ["117731", "204956", "338102", "462587"]


@pytest.mark.parametrize("mark", [":", "#", "|", "\\", ".", " : "])
def test_a_registered_number_is_found_again_whatever_separates_its_parts(release, mark):
    numbers = [f"KGH{mark}25{mark}{serial}" for serial in SERIALS]
    files = {"registration.csv": table(["Patient Name", "UHID"],
                                       [[n, i] for n, i in zip(PEOPLE, numbers, strict=True)]),
             "billing.csv": table(["bill_ref", "remarks"],
                                  [[f"B-{k}", f"paid against {i} in cash"]
                                   for k, i in enumerate(numbers)]),
             "note.txt": f"Seen again today. {numbers[0]} reviewed at 10:30, BP 140/90.\n",
             "kgh_25_204956.txt": "Dressing changed.\n"}
    out = release(files)
    text = released(out)
    for serial in SERIALS:
        assert serial not in text
    assert "10:30" in text and "140/90" in text
    claims = out.claims()
    note = [p for (name, _), p in claims.items() if name == "note.txt"]
    assert note and note[0] == claims[("registration.csv", "row:0")]


def test_times_ratios_pressures_and_dates_are_not_taken_for_numbers(release):
    numbers = [f"KGH:25:{serial}" for serial in SERIALS]
    out = release({"registration.csv": table(["Patient Name", "UHID"],
                                             [[n, i] for n, i in zip(PEOPLE, numbers,
                                                                     strict=True)]),
                   "note.txt": "Dose at 10:30. Adrenaline 1:1000. BP 140/90. Ratio 25:11.\n"})
    text = out.text("note.txt")
    for kept in ("10:30", "1:1000", "140/90", "25:11"):
        assert kept in text


# ------------------------------------------------------------ degrees after a doctor (G3)


@pytest.mark.parametrize(("patient", "doctor"), [
    ("Anurag Pathak", "Anurag Sehgal"),
    ("Mohammad Danish", "Danish Qureshi"),
    ("अनुराग पाठक", "अनुराग सहगल"),
])
def test_a_doctor_and_their_degrees_survive_a_shared_given_name(release, patient, doctor):
    lines = [f"Consultant: Dr. {doctor}, MD, DM (Neurology)",
             f"Seen by Dr {doctor}, MBBS, MD",
             f"{doctor}, MD, DM",
             f"डॉ. {doctor}, एम.डी., डी.एम."]
    out = release({"a.txt": f"Patient Name: {patient}\nUHID: KMC-100417\n"
                            + "\n".join(lines) + "\n"})
    text = out.text("a.txt")
    assert patient not in text
    for line in lines:
        assert line in text


@pytest.mark.parametrize("line", [
    "Patient Name: Md Danish Qureshi",
    "Patient Name: MD DANISH",
    "Md. Danish ko aaj bukhar hai",
    "M.D. Danish Qureshi admitted yesterday",
    "S/O Md Danish",
])
def test_the_short_form_of_a_name_is_not_taken_for_a_degree(release, line):
    out = release({"a.txt": f"{line}\nUHID: KMC-100417\n",
                   "reg.csv": table(["Patient Name"], [["Md Danish Qureshi"]])})
    assert "danish" not in out.text("a.txt").casefold()


# --------------------------------------------------- medicines after a dosage form (G2)

# invented medicine names that are in no list


@pytest.mark.parametrize(("line", "drug"), [
    ("Inj Velcarimod 200 mg IV given.", "Velcarimod"),
    ("Tab Zorivanta 1 mg 1-0-1", "Zorivanta"),
    ("Cap. Pelmoxane Forte 250mg BD", "Pelmoxane Forte"),
    ("Syp Ravinderol 5 ml at night", "Ravinderol"),
    ("Injection Santoplex SC stat", "Santoplex"),
])
def test_an_unlisted_medicine_before_its_dose_is_kept(release, line, drug):
    out = release({"a.txt": f"Patient Name: Pushpa Toppo\n{line}\n"})
    assert drug in out.text("a.txt")


@pytest.mark.parametrize("line", [
    "Gave the Tab Pushpa Toppo was asking for.",
    "Report in Tab Pushpa Toppo shared yesterday",
])
def test_a_name_after_a_dosage_form_with_no_dose_is_still_removed(release, line):
    out = release({"a.txt": f"Patient Name: Pushpa Toppo\n{line}\n"})
    assert "Pushpa" not in out.text("a.txt")


@pytest.mark.parametrize("line", ["Alcohol: denies", "Tobacco: nil", "Gutka: occasional",
                                  "Smoking: never", "Betel: chews daily"])
def test_social_history_words_are_kept(release, line):
    out = release({"a.txt": f"Patient Name: Pushpa Toppo\n{line}\n"})
    assert line in out.text("a.txt")


# ------------------------------------------------- names inside a relation object (G5)

UNLISTED = "Zorinsanga"          # one word, in no name list


@pytest.mark.parametrize("box", [
    "relation", "relative", "kin", "next_of_kin", "nok", "guardian", "attendant",
    "emergency_contact", "father", "mother", "husband", "wife", "spouse", "son", "daughter"])
@pytest.mark.parametrize("key", ["name", "full_name", "naam"])
def test_a_name_under_a_relation_key_is_a_relative(release, box, key):
    out = release({"a.json": json.dumps({"patientName": "Pushpa Toppo", "uhid": "KMC-100417",
                                         box: {key: UNLISTED}})})
    assert UNLISTED not in out.text("a.json")


@pytest.mark.parametrize("kin", ["husband", "पति", "पत्नी", "Mother"])
def test_a_name_beside_a_kinship_word_is_a_relative(release, kin):
    out = release({"a.json": json.dumps({
        "patientName": "Pushpa Toppo", "uhid": "KMC-100417",
        "linked": {"type": kin, "name": UNLISTED}}, ensure_ascii=False)})
    assert UNLISTED not in out.text("a.json")


# ------------------------------------------- a patient-name key under any container (G4)


@pytest.mark.parametrize("box", ["people", "persons", "records", "admissions", "entries",
                                 "visits", "cases", "registrations", "members", "contact",
                                 "reported_by", "informant", "wards_staff"])
@pytest.mark.parametrize("key", ["patientName", "pt_name", "patient_full_name"])
def test_a_key_that_says_patient_names_the_patient_under_any_container(release, box, key):
    record = {"crn_no": "KMC-100417", key: "Thangkhuma"}
    out = release({"a.json": json.dumps({box: [record]}),
                   "b.txt": "Patient Name: Thangkhuma\nUHID: KMC-100417\n"})
    claims = out.claims()
    first = next(p for (name, _), p in claims.items() if name == "a.json")
    assert PSN.match(first or "") and first == claims[("b.txt", "doc")]
    assert first in out.text("a.json")


@pytest.mark.parametrize("box", ["contact", "reported_by", "guardian", "nurse"])
@pytest.mark.parametrize("key", ["patientName", "pt_name", "patient_full_name"])
def test_the_container_does_not_override_a_patient_key(box, key):
    assert classify_json_key((box, key)) is F.PATIENT_NAME


def test_an_unlisted_relative_does_not_cost_the_patient_their_pseudonym(release):
    record = {"crn_no": "KMC-100417", "patientName": "Thangkhuma",
              "relation": {"type": "husband", "name": UNLISTED}}
    out = release({"a.json": json.dumps({"people": [record]})})
    saved = json.loads(out.text("a.json"))["people"][0]
    assert PSN.match(saved["patientName"])
    assert saved["relation"]["name"] == "[RELATIVE]"

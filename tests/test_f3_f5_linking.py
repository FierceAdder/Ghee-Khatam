"""Compound given names, registered phones in notes and file-local groups.

Every rule has a pair: something that must be linked and something close to it that must
stay apart. All names and numbers are invented.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from wards.link import names_compatible, parse_name

PSN = re.compile(r"^PSN-[0-9A-HJKMNP-TV-Z]{6}$")


# --------------------------------------------------------- compound given names (F3)


@pytest.mark.parametrize(("one", "other"), [
    ("Ram Kumar", "Ramkumar"),
    ("Mohammad Ali", "Mohammadali"),
    ("Ram-Kripal", "Ram Kripal"),
    ("Ramkripal", "Ram-Kripal"),
    ("राम कुमार", "रामकुमार"),
    ("मोहम्मद अली", "मोहम्मदअली"),
    ("राम-कृपाल", "रामकृपाल"),
    ("रामकुमार", "Ram Kumar"),
    ("Ramkumar Tripathi", "Ram Kumar Tripathi"),
    ("Ramkumar", "Ram Kumar Tripathi"),
    ("Ramkumar Tripathi", "Ram Kumar"),
    ("Rambabu Tripathi", "Ram Babu Tripathi"),
])
def test_joined_and_split_given_names_are_one_name(lexicon, one, other):
    a, b = parse_name(one, lexicon), parse_name(other, lexicon)
    assert names_compatible(a, b) and names_compatible(b, a)


@pytest.mark.parametrize(("one", "other"), [
    ("Ram Kumar", "Ramesh Kumar"),
    ("राम कुमार", "रमेश कुमार"),
    ("Ramkumar", "Ram"),
    ("Ramkumar", "Rajkumar"),
    ("Ramkumar Tripathi", "Ram Kumar Bajpai"),
    ("Ramkumari Tripathi", "Ram Kumar Tripathi"),
    ("रामकुमारी त्रिपाठी", "राम कुमार त्रिपाठी"),
    ("Mohammadali", "Mohammad Arif"),
])
def test_different_compound_names_stay_apart(lexicon, one, other):
    a, b = parse_name(one, lexicon), parse_name(other, lexicon)
    assert not names_compatible(a, b) and not names_compatible(b, a)


@pytest.mark.parametrize(("joined", "split"), [
    ("मरीज़ का नाम: रामबहादुर त्रिपाठी", "मरीज़ का नाम: राम बहादुर त्रिपाठी"),
    ("Patient Name: Shivkumar Dubey", "Patient Name: Shiv Kumar Dubey"),
    ("Patient Name: Ramsewak Bajpai", "Patient Name: Ram-Sewak Bajpai"),
])
def test_same_hospital_number_links_joined_and_split_spellings(release, joined, split):
    result = release({"a.txt": f"{joined}\nUHID: KMC-100417\n",
                      "b.txt": f"{split}\nUHID: KMC-100417\n"})
    a, b = result.claims()[("a.txt", "doc")], result.claims()[("b.txt", "doc")]
    assert PSN.match(a) and a == b


def test_ram_kumar_and_ramesh_kumar_with_different_numbers_stay_apart(release):
    result = release({"a.txt": "Patient Name: Ram Kumar\nUHID: KMC-100417\n",
                      "b.txt": "Patient Name: Ramesh Kumar\nUHID: KMC-200528\n",
                      "c.txt": "Patient Name: Ramkumar\nUHID: KMC-200528\n"})
    claims = result.claims()
    assert claims[("a.txt", "doc")] != claims[("b.txt", "doc")]
    # nor does the joined spelling bridge to the other man's number
    assert claims[("c.txt", "doc")] != claims[("b.txt", "doc")]


# ------------------------------------------------- a registered phone in a note (F4)

PHONE = "9812304567"
HEADER = "UHID,Patient Name,Gender,Age,Mobile\n"
PATIENT = f"KMC-1001,Harbati Chaurasia,F,67,{PHONE}\n"
NOTE = ("note.txt", "doc")


def with_registry(release, rows: str = PATIENT, **notes: str):
    files = {"registry.csv": HEADER + rows}
    files.update({f"{name}.txt": text for name, text in notes.items()})
    return release(files)


def linked(result, name: str = "note") -> str | None:
    return result.claims()[(f"{name}.txt", "doc")]


def patient_of(result, row: int = 0) -> str:
    return result.claims()[("registry.csv", f"row:{row}")]


@pytest.mark.parametrize("note", [
    # the surname form is the only name of the patient, and its owner is not stated
    f"Handover - Mrs. Chaurasia, bed 7. Relative Pintu Kumar (son) {PHONE}.\n",
    f"श्रीमती हरबती का हाल ठीक। बेटे का नंबर {PHONE}\n",
    f"Chaurasia ji ko bukhar hai. Beta ko call kiya - {PHONE}.\n",
])
def test_registered_phone_and_the_patients_short_name_link_a_note(release, note):
    result = with_registry(release, note=note)
    assert linked(result) == patient_of(result)
    assert linked(result) in result.text("note.txt")
    assert PHONE not in result.everything()


def test_nursing_note_and_handover_with_different_short_names_both_link(release):
    # "hand" sorts first, so the note was once compared only with the first name there
    result = with_registry(
        release,
        hand=f"WARD HANDOVER\nHarbati ji, sugar high. Son ko inform karna - {PHONE}\n",
        note=f"Chaurasia ji ko raat se bukhar hai. Bete ko call kiya - {PHONE}.\n")
    assert linked(result, "hand") == linked(result, "note") == patient_of(result)


@pytest.mark.parametrize("note", [
    f"Patient Name: Dinanath Kushwaha\nAttendant ko call kiya - {PHONE}.\n",
    f"Pt Dinanath ko bukhar. Beta ko call kiya - {PHONE}\n",
    f"Handover - Mrs. Kushwaha, bed 7. Son {PHONE}.\n",
    f"Handover - Mr. Chaurasia, bed 7. Son {PHONE}.\n",          # a man: not her
    f"Devi ji ko bukhar. Beta ko call kiya - {PHONE}\n",         # names nobody
    f"Bed 7 patient stable. Beta ko call kiya - {PHONE}.\n",     # no name at all
    f"Bed 7 patient stable. S/o Chaurasia, ph {PHONE}.\n",       # only a relative's name
    f"Handover: Mrs. Harbati Chaurasia and Mrs. Phulmati Kushwaha. Son {PHONE}.\n",
])
def test_registered_phone_without_the_patients_name_links_nothing(release, note):
    result = with_registry(release, note=note)
    assert linked(result) != patient_of(result)
    assert PHONE not in result.everything()


@pytest.mark.parametrize("note", [
    f"Pt Dinanath ko bukhar. Beta ko call kiya - {PHONE}\n",
    f"Handover - Mrs. Kushwaha, bed 7. Son {PHONE}.\n",
])
def test_phone_with_another_persons_name_is_counted_as_shared(release, note):
    result = with_registry(release, note=note)
    assert "skipped_shared_phone=1 " in result.log


@pytest.mark.parametrize("note", [
    f"Chaurasia ji ko bukhar. Beta ko call kiya - {PHONE}\n",
    f"Chaurasia ji ko bukhar. Beta Pintu ko call kiya - {PHONE}\n",
])
def test_surname_that_fits_two_people_on_one_phone_is_not_guessed(release, note):
    result = with_registry(
        release, PATIENT + f"KMC-1002,Phulmati Chaurasia,F,40,{PHONE}\n", note=note)
    assert patient_of(result, 0) != patient_of(result, 1)
    assert linked(result) not in {patient_of(result, 0), patient_of(result, 1)}


def test_a_note_still_links_when_one_persons_records_disagree(release):
    """Two sets of records of one name cannot join (their ages differ); that is no reason
    to leave a note with the same name and phone to a weaker clue, which picked a namesake."""
    header = "uhid,patient_name,sex,age,mobile\n"
    out = release({
        "a.csv": header + f"KMC-2001,Devendra Chaurasia,M,30,{PHONE}\n",
        "b.csv": header + f"KMC-2002,Devendra Chaurasia,M,61,{PHONE}\n",
        "note.txt": f"Patient Name: Devendra Chaurasia\nPhone No: {PHONE}\nBP 120/80\n"})
    claims = out.claims()
    assert claims[("note.txt", "doc")] == claims[("a.csv", "row:0")] is not None


def test_a_first_name_picks_one_of_two_people_on_a_family_phone(release):
    result = with_registry(
        release, PATIENT + f"KMC-1002,Phulmati Chaurasia,F,40,{PHONE}\n",
        note=f"Phulmati ji ko bukhar. Beta ko call kiya - {PHONE}\n")
    assert linked(result) == patient_of(result, 1)


def test_hub_phone_still_links_no_note(release):
    result = with_registry(
        release,
        PATIENT + f"KMC-1002,Dinanath Kushwaha,M,48,{PHONE}\n"
                  f"KMC-1003,Phulmati Bajpai,F,30,{PHONE}\n",
        note=f"Handover - Mrs. Chaurasia, bed 7. Son {PHONE}.\n")
    assert linked(result) is None


def test_an_unregistered_phone_links_no_short_name(release):
    result = release({
        "a.txt": f"Handover - Mrs. Chaurasia, bed 7. Son {PHONE}.\n",
        "b.txt": f"Handover - Mrs. Chaurasia, bed 9. Son {PHONE}.\n",
    })
    assert result.claims()[("a.txt", "doc")] is None
    assert result.claims()[("b.txt", "doc")] is None


# ------------------------------------------------------------ file-local groups (F5)

LAB = "sample_id,patient_name,age_sex,test,result,collected_on\n"


def lab(release, *rows: str, header: str = LAB, **options):
    result = release({"lab.csv": header + "".join(row + "\n" for row in rows)}, **options)
    claims = [result.claims().get(("lab.csv", f"row:{i}")) for i in range(len(rows))]
    cells = [line.split(",") for line in result.text("lab.csv").splitlines()[1:]]
    return result, claims, cells


def day(cell: str) -> dt.date:
    return dt.datetime.strptime(cell, "%d/%m/%Y").date()


def shifts(cells: list, originals: list) -> list[int]:
    return [(day(row[-1]) - day(before)).days
            for row, before in zip(cells, originals, strict=True)]


def test_rows_of_one_specimen_share_a_pseudonym_and_a_date_shift(release):
    _, claims, cells = lab(release,
                           "LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                           "LB260077,Devendra Rawat,45/M,Urea,30,12/03/2026")
    assert PSN.match(claims[0]) and claims[0] == claims[1]
    assert cells[0][1] == cells[1][1] == claims[0]
    assert cells[0][-1] == cells[1][-1] != "12/03/2026"


def test_same_name_with_a_different_age_is_two_groups(release):
    _, claims, cells = lab(release,
                           "LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                           "LB260077,Devendra Rawat,45/M,Urea,30,12/03/2026",
                           "LB260078,Devendra Rawat,23/M,Urea,31,12/03/2026",
                           "LB260079,Devendra Rawat,23/M,Sodium,139,12/03/2026")
    assert claims[0] == claims[1] != claims[2] == claims[3]
    assert cells[0][-1] == cells[1][-1] and cells[2][-1] == cells[3][-1]
    assert cells[0][-1] != cells[2][-1]


def test_a_specimen_number_shared_by_two_names_groups_nobody(release):
    _, claims, cells = lab(release,
                           "LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                           "LB260077,Girish Rawat,45/M,Urea,30,12/03/2026",
                           "LB260077,Devendra Rawat,45/F,Sodium,139,02/04/2026")
    assert len(set(claims)) == 3
    assert len({row[1] for row in cells}) == 3


def test_a_nameless_row_of_a_specimen_moves_with_it_but_claims_nothing(release):
    result, claims, cells = lab(release,
                                "LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                                "LB260077,,,Urea,30,14/03/2026")
    assert PSN.match(claims[0]) and claims[1] is None
    moved = shifts(cells, ["12/03/2026", "14/03/2026"])
    assert moved[0] == moved[1] != 0


def test_short_visit_counters_are_not_visit_numbers(release):
    _, claims, _ = lab(release,
                       "1,Devendra,45/M,Haemoglobin,13.5,12/03/2026",
                       "1,Devendra,45/M,Urea,30,19/06/2026",
                       header=LAB.replace("sample_id", "visit_no"))
    assert claims == [None, None]


def test_a_visit_number_groups_the_rows_of_a_json_export(release):
    rows = [{"visit_id": "OPD/26/0042", "patient_name": "Devendra Rawat", "visit_date": d}
            for d in ("2026-03-12", "2026-03-12")]
    result = release({"visits.json": json.dumps({"visits": rows})})
    claims = result.claims()
    assert claims[("visits.json", "/visits/0")] == claims[("visits.json", "/visits/1")]
    out = json.loads(result.text("visits.json"))["visits"]
    assert out[0]["visit_date"] == out[1]["visit_date"] != "2026-03-12"


NO_NUMBER = "patient_name,age_sex,test,result,collected_on\n"


def test_same_name_age_sex_and_date_in_one_file_is_one_group(release):
    _, claims, cells = lab(release,
                           "Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                           "DEVENDRA RAWAT,45/M,Urea,30,12/03/2026", header=NO_NUMBER)
    assert PSN.match(claims[0]) and claims[0] == claims[1]
    assert cells[0][-1] == cells[1][-1]


def test_same_name_age_sex_on_different_dates_shares_a_shift_without_claiming(release):
    originals = ["12/03/2026", "19/06/2026"]
    _, claims, cells = lab(release,
                           f"Devendra Rawat,45/M,Haemoglobin,13.5,{originals[0]}",
                           f"Devendra Rawat,45/M,Urea,30,{originals[1]}", header=NO_NUMBER)
    assert cells[0][0] == cells[1][0] and PSN.match(cells[0][0])
    assert len(set(shifts(cells, originals))) == 1
    assert claims == [cells[0][0], None]        # no link is reported on this alone


def test_a_later_visit_does_not_withdraw_the_claim_for_one_days_rows(release):
    _, claims, cells = lab(release,
                           "Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                           "Devendra Rawat,45/M,Urea,30,12/03/2026",
                           "Devendra Rawat,45/M,Sodium,139,19/06/2026", header=NO_NUMBER)
    assert claims == [cells[0][0], cells[0][0], None]
    assert len({row[0] for row in cells}) == 1


@pytest.mark.parametrize(("header", "first", "second"), [
    # different hospital numbers, villages, relatives, phones or sexes: two people
    ("uhid,", "KMC-1001,", "KMC-2002,"),
    ("village,", "Bewar,", "Ghiror,"),
    ("relative_name,", "Shyam Rawat,", "Vijay Rawat,"),
    ("mobile,", "9812304567,", "9012345678,"),
    ("pin,", "205001,", "209625,"),
])
def test_homonyms_in_one_file_with_a_differing_detail_stay_apart(release, header, first, second):
    rows = [first + "Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
            second + "Devendra Rawat,45/M,Urea,30,12/03/2026"]
    _, claims, cells = lab(release, *rows, header=header + NO_NUMBER)
    assert claims[0] != claims[1]
    assert cells[0][1] != cells[1][1]


def test_a_row_that_fits_two_homonyms_joins_neither(release):
    _, claims, _ = lab(release,
                       "KMC-1001,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                       "KMC-2002,Devendra Rawat,45/M,Urea,30,12/03/2026",
                       ",Devendra Rawat,45/M,Sodium,139,12/03/2026",
                       header="uhid," + NO_NUMBER)
    assert len(set(claims)) == 3


def test_local_groups_never_cross_files_or_join_linked_patients(release):
    rows = ["LB260077,,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
            "LB260077,,Devendra Rawat,45/M,Urea,30,12/03/2026"]
    header = "sample_id,uhid,patient_name,age_sex,test,result,collected_on\n"
    linked_row = "LB260077,KMC-1001,Devendra Rawat,45/M,Sodium,139,12/03/2026\n"
    result = release({
        "one.csv": header + "".join(r + "\n" for r in rows) + linked_row,
        "two.csv": header + rows[0] + "\n",
        "note.txt": "Patient Name: Devendra Rawat\nUHID: KMC-1001\n",
    })
    claims = result.claims()
    group = claims[("one.csv", "row:0")]
    assert group == claims[("one.csv", "row:1")]
    registered = claims[("note.txt", "doc")]
    assert claims[("one.csv", "row:2")] == registered != group
    assert claims[("two.csv", "row:0")] not in {group, registered}


def test_local_groups_follow_the_secret(release):
    rows = ["LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
            "LB260077,Devendra Rawat,45/M,Urea,30,12/03/2026"]
    one = lab(release, *rows, secret=b"first")
    again = lab(release, *rows, secret=b"first")
    two = lab(release, *rows, secret=b"second")
    assert one[0].text("lab.csv") == again[0].text("lab.csv")
    assert one[1][0] != two[1][0]


def test_local_group_counts_reach_the_log(release):
    result, _, _ = lab(release,
                       "LB260077,Devendra Rawat,45/M,Haemoglobin,13.5,12/03/2026",
                       "LB260077,Devendra Rawat,45/M,Urea,30,12/03/2026")
    assert "merged_local=1 " in result.log and "Rawat" not in result.log

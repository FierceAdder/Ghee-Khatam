"""Hospital numbers of shapes no rule lists.

Three routes recognise them: a field whose values look alike and sit beside patients, a
label ending in a number word, and a code that keeps appearing with one patient's name.
Each is paired with a number of the same look that must survive. All names and numbers here
are invented.
"""

from __future__ import annotations

import json

import pytest

from wards.detect.ids import detect_ids
from wards.model import EntityType as E

PEOPLE = ["Lalrinzuali Hmar", "Pushpa Toppo", "Dechen Bhutia", "Kiran Mahato"]
NUMBERS = ["TQV/R/3318", "TQV/R/5527", "TQV/R/8043", "TQV/R/9164"]
CLAIMS = ["RVN/Q/77104", "RVN/Q/77219", "RVN/Q/77388", "RVN/Q/77451"]


def table(header: list[str], rows: list[list[str]]) -> str:
    return "".join(",".join(line) + "\n" for line in [header, *rows])


def only_name(out, suffix: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((out.root / "sanitized").rglob("*" + suffix)))


def released(out) -> str:
    """Released file names and contents. Bare digits are searched only here: the log and the
    report hold digests, in which any short run of digits can turn up by chance."""
    folder = out.root / "sanitized"
    return "\n".join(f"{p.relative_to(folder)}\n{p.read_text(encoding='utf-8')}"
                     for p in sorted(folder.rglob("*")) if p.is_file())


# ------------------------------------------------------------------ route 1: fields


def test_a_number_under_an_unseen_key_goes_and_the_claim_number_stays(release):
    rows = [{"patient_name": name, "pt_trace": number, "claim_ref": claim,
             "diagnosis": "Dengue fever"}
            for name, number, claim in zip(PEOPLE, NUMBERS, CLAIMS, strict=True)]
    out = release({"export.json": json.dumps({"rows": rows})})
    text = out.text("export.json")
    for number, claim in zip(NUMBERS, CLAIMS, strict=True):
        assert number not in text
        assert claim in text
    assert len(set(out.claims().values())) == len(PEOPLE)


def test_without_any_header_clue_a_new_number_on_every_row_is_an_event(release):
    rows = []
    for index, (name, number) in enumerate(zip(PEOPLE, NUMBERS, strict=True)):
        for turn in range(2):
            rows.append([name, number, f"WQ-{4100 + 10 * index + turn}-K", "Haemoglobin"])
    out = release({"results.csv": table(["Patient Name", "k7", "k9", "Test"], rows)})
    text = out.text("results.csv")
    for row in rows:
        assert row[1] not in text
        assert row[2] in text


def test_single_record_files_are_typed_together(release):
    files = {f"r{i}.json": json.dumps({"patient": {"name": name, "zz": f"ZK-2024-{1187 + i}"},
                                       "result": "Platelets 1.5 lakh"})
             for i, name in enumerate(PEOPLE)}
    out = release(files)
    for i in range(len(PEOPLE)):
        assert f"ZK-2024-{1187 + i}" not in out.text(f"r{i}.json")


def test_an_unseen_form_label_in_notes_goes_and_a_specimen_label_stays(release):
    files = {f"n{i}.txt": f"Patient Name: {name}\nKramak :- {number}\n"
                          f"Specimen :- {claim}\nAdvice: plenty of fluids\n"
             for i, (name, number, claim) in enumerate(
                 zip(PEOPLE, NUMBERS, CLAIMS, strict=True))}
    out = release(files)
    for i in range(len(PEOPLE)):
        text = out.text(f"n{i}.txt")
        assert NUMBERS[i] not in text
        assert CLAIMS[i] in text


def test_a_code_shared_by_different_people_is_kept_and_joins_nobody(release):
    rows = [[name, "NHX/PK/4410", "Dengue fever"] for name in PEOPLE]
    out = release({"claims.csv": table(["Patient Name", "k4", "Diagnosis"], rows)})
    assert out.text("claims.csv").count("NHX/PK/4410") == len(PEOPLE)
    assert len(set(out.claims().values())) == len(PEOPLE)


def test_the_number_leaves_other_files_keys_and_file_names(release):
    rows = [[name, number] for name, number in zip(PEOPLE, NUMBERS, strict=True)]
    out = release({
        "list.csv": table(["Patient Name", "pt_trace"], rows),
        "scans/film_3318.txt": "Chest film reviewed. No consolidation.\n",
        "scans/TQV_R_5527.txt": "Ultrasound normal.\n",
        "handover.txt": "Bed side file tqv-r-8043 kept at the desk.\n",
        "index.json": json.dumps({"TQV/R/9164": "film sent",
                                  "source_path": "D:/exports/9164/film.dcm"}),
    })
    everything = out.everything()
    for number in NUMBERS:
        core = number.rsplit("/", 1)[1]
        assert number not in everything and number.lower() not in everything.lower()
        assert f"film_{core}" not in everything and f"R_{core}" not in everything
        assert f"exports/{core}" not in everything
    assert "tqv-r-8043" not in everything
    assert "No consolidation" in everything


def test_the_number_links_a_table_row_to_a_note_that_only_has_a_first_name(release):
    rows = [[name, number] for name, number in zip(PEOPLE, NUMBERS, strict=True)]
    out = release({"list.csv": table(["Patient Name", "pt_trace"], rows),
                   "note.txt": "Patient Name: Pushpa\nFile TQV/R/5527 reviewed today.\n"})
    claims = out.claims()
    assert claims[("note.txt", "doc")] == claims[("list.csv", "row:1")] is not None


def test_records_about_nobody_stay_untouched(release):
    inventory = table(["k1", "k2"], [[number, "Infusion pump"] for number in NUMBERS])
    memo = ("Stock check\nEquipment No. 551210\nBatch No: QX55121\n"
            "Protocol ref NHX/PK/4410 revised. Reorder code NHX/PK/4410.\n")
    out = release({"inventory.csv": inventory, "memo.txt": memo})
    assert out.text("inventory.csv") == inventory
    assert out.text("memo.txt") == memo


# ------------------------------------------------------------------ route 2: labels


@pytest.mark.parametrize("line, number", [
    ("IPD No.:- 26/K/00417", "26/K/00417"),
    ("Indoor Reg. No. – K-4417-B", "K-4417-B"),
    ("I.P. No :- QD 44170", "QD 44170"),
    ("Hosp Ref # QD-88-127", "QD-88-127"),
    ("भर्ती सं. 26/0417", "26/0417"),
    ("Pt ID:- 44/QD/9", "44/QD/9"),
    ("File shifted with IP-26-0417 to the ward", "IP-26-0417"),
    ("Old record MRD/4417 requested", "MRD/4417"),
])
def test_a_number_after_a_number_word_is_a_hospital_number(line, number):
    assert (E.MRN, number) in [(m.etype, m.value) for m in detect_ids(line)]


def test_an_unknown_kind_of_number_is_removed_but_never_links():
    found = [(m.etype, m.value) for m in detect_ids("Kiosk No. QD-88127")]
    assert found == [(E.OTHER_ID, "QD-88127")]


@pytest.mark.parametrize("line", [
    "ICD K50.9", "ICD No. K50.9", "ICD-10 ID: A15.0", "LOINC No 718-7", "Bill No. QD/88127",
    "Sample ID LB-44-170", "Bed No. 1044", "Batch No: QX55121", "Dose No. 500mg",
    "Review No: 14/03/2026", "Visit ID: OPD/26/03/00017", "There is no 250 rise today",
    "Package No. NHX-4410", "Lab No. LB/44170", "बिल सं. 88127", "Platelet Num: 150000",
    "Vial No. 100 mg", "मो. नं. 044-17",
])
def test_other_numbers_are_left_alone(line):
    assert [m for m in detect_ids(line) if m.etype in (E.MRN, E.OTHER_ID)] == []


def test_labelled_numbers_end_to_end(release):
    note = ("Patient Name: Dechen Bhutia\nIPD No.:- 26/K/00417\nDiagnosis: ICD K50.9\n"
            "Bill No. QD/88127\n")
    out = release({"ward/note_00417.txt": note,
                   "ward/slip.txt": "Send file 26/K/00417 back to records.\n"})
    everything = out.everything()
    assert "00417" not in released(out) and "26/K/00417" not in everything
    assert "K50.9" in everything and "QD/88127" in everything


def test_a_labelled_number_shared_by_two_people_does_not_join_them(release):
    out = release({
        "a.txt": "Patient Name: Dechen Bhutia\nIndoor No.:- NHX/55/4410\n",
        "b.txt": "Patient Name: Kiran Mahato\nIndoor No.:- NHX/55/4410\n",
        "c.txt": "Indoor No.:- NHX/55/4410\n"})
    assert "NHX/55/4410" not in out.everything()
    claims = out.claims()
    first, second = claims[("a.txt", "doc")], claims[("b.txt", "doc")]
    assert first != second
    assert claims.get(("c.txt", "doc")) not in (first, second)


# ------------------------------------------------------------ route 3: co-occurrence


def test_a_code_seen_twice_with_one_name_is_that_patients_number(release):
    out = release({
        "a.txt": "Patient Name: Pushpa Toppo\nCame with slip WQX7/4417K today.\n",
        "b.txt": "Patient Name: Pushpa Toppo\nWQX7/4417K shows old film.\n",
        "films/WQX7_4417K.txt": "Film normal.\n"})
    assert "4417" not in released(out) and "WQX7" not in out.everything()
    claims = out.claims()
    assert claims[("a.txt", "doc")] == claims[("b.txt", "doc")] is not None


def test_a_code_two_patients_share_stays_and_joins_nobody(release):
    files = {}
    for i, name in enumerate(["Pushpa Toppo", "Pushpa Toppo", "Kiran Mahato"]):
        files[f"n{i}.txt"] = f"Patient Name: {name}\nPlanned under HBP/SG/4410 scheme.\n"
    files["n3.txt"] = "Patient Name: Kiran Mahato\nImplant HBP/SG/4410 used.\n"
    out = release(files)
    for name in files:
        assert "HBP/SG/4410" in out.text(name)
    claims = out.claims()
    assert claims[("n0.txt", "doc")] != claims[("n2.txt", "doc")]


def test_repeated_specimen_bill_and_medicine_codes_stay(release):
    note = ("Patient Name: Pushpa Toppo\nSample ID: LBX/44170\nBill No. QDX/88127\n"
            "Tab Zerodol-1000 given. Claim ref CRX/55093.\n")
    out = release({"a.txt": note, "b.txt": note + "Seen again.\n"})
    text = out.text("a.txt") + out.text("b.txt")
    for kept in ("LBX/44170", "QDX/88127", "Zerodol-1000", "CRX/55093"):
        assert text.count(kept) == 2


def test_a_code_seen_with_a_name_only_once_is_not_enough(release):
    out = release({"a.txt": "Patient Name: Pushpa Toppo\nCame with slip WQX7/4417K today.\n",
                   "b.txt": "Store room shelf WQX7/4417K cleaned.\n"})
    assert "WQX7/4417K" in out.text("b.txt")

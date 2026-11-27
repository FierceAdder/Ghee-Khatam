"""The attack replay: how it reads evidence, and that it agrees with the organisers.

The reading tests build a tiny invented attack kit, so they always run. The calibration
tests compare the replay with the organisers' result on the raw practice input and are
skipped without the practice kit.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))

from attacker import Attacker, Settings, _references  # noqa: E402

TOOLKIT = Path(os.environ.get("TOOLKIT_DIR", ROOT.parent / "Secret-Wards-Toolkit"))
TRUTH = TOOLKIT / "dev" / "truth" / "truth.json"
kit = pytest.mark.skipif(not (TRUTH.parent / "raw_attack.json").is_file(),
                         reason="practice kit not available")

# --------------------------------------------------------------------- invented kit

COLUMNS = ("rid", "name", "relation", "relative_name", "gender", "age", "house_no", "mohalla",
           "village", "tehsil", "district", "pin", "phone", "email", "aadhaar_last4")
PEOPLE = (
    ("R1", "Wali Zaveri", "S/o", "Ekambar Zaveri", "M", 47, "12", "", "Neemkhera", "Motiganj",
     "Kesarpur", "299001", "7000000001", "", "9012"),
    ("R2", "Omkar Pillai", "S/o", "Hari Pillai", "M", 47, "31", "", "Belwadi", "Sonapur",
     "Ratangarh", "298002", "", "", ""),
)


@pytest.fixture(scope="module")
def invented(tmp_path_factory):
    base = tmp_path_factory.mktemp("kit")
    for name in ("attacker", "truth", "input"):
        (base / name).mkdir()
    with open(base / "attacker" / "registry.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(PEOPLE)
    fact = {"rid": "R1", "visit_dates": ["2026-03-04"], "hospital": "Sunrise Hospital",
            "condition_hint": "Typhoid fever"}
    (base / "attacker" / "known_facts.jsonl").write_text(json.dumps(fact) + "\n", "utf-8")
    (base / "input" / "note.txt").write_text(
        "Diagnosis: Typhoid fever (A01.0); Essential (primary) hypertension (I10)\n"
        "निदान: टाइफाइड (A01.0)\n", "utf-8")
    truth = {"files": {}, "patients": {"P1": {"rid": "R1"}}}
    (base / "truth" / "truth.json").write_text(json.dumps(truth), "utf-8")
    return base / "truth" / "truth.json"


@pytest.fixture(scope="module")
def reader(invented):
    return Attacker(invented.parents[1], invented)


def read(attacker, text):
    return attacker.extract(text, "dmy", "")


def test_a_fourteen_digit_health_id_is_not_an_aadhaar(reader):
    assert read(reader, "ABHA No: 91-1234-5678-3456").last4 == set()
    assert read(reader, "Aadhaar No: 5234 5678 9012").last4 == {"9012"}
    assert read(reader, "Aadhaar No: XXXX XXXX 9012").last4 == {"9012"}


def test_the_digits_of_a_resource_identifier_are_not_a_phone_number(reader):
    assert read(reader, "urn:uuid:93368d52-86de-4263-8902-719343ba0fb1").phones == set()
    assert read(reader, "Mobile: 70000-00001").phones == {"7000000001"}


@pytest.mark.parametrize("text, ages", [
    ("मरीज़ 47 वर्ष, बुखार", {(47, 47)}), ("उम्र समूह [40,50)", {(40, 49)}),
    ("बुखार 8 वर्ष से", set()), ("शुगर x 8 वर्ष", set()), ("पिछले 8 वर्ष में", set()),
])
def test_age_forms_but_not_durations(reader, text, ages):
    assert read(reader, text).ages == ages


def test_places_are_read_without_a_label_but_not_from_a_facility_name(reader):
    found = read(reader, "Patient came from Neemkhera, Kesarpur for review")
    assert len(found.villages) == 1 and len(found.districts) == 1
    for facility in ("Sunrise Hospital, Kesarpur", "Metro Labs (Kesarpur)",
                     "District Hospital Kesarpur"):
        assert read(reader, facility).districts == set(), facility
    # a tehsil's name alone is not guessed without its label
    assert read(reader, "Motiganj road").tehsils == set()
    assert len(read(reader, "Tehsil Motiganj").tehsils) == 1


def test_a_registry_name_that_is_also_an_ordinary_word_is_looked_up(reader):
    wali = reader.lexicon.name_key("Wali")
    assert wali in read(reader, "Name: Wali Zaveri").names
    assert wali not in read(reader, "Seen by Dr. Wali Zaveri today").names


def test_a_relative_cue_is_a_whole_word_on_the_same_line(reader):
    wali = reader.lexicon.name_key("Wali")
    # "Hospital" holds the letters of the Hindi-English word "pita" (father)
    assert wali in read(reader, "City Hospital\nName: Wali").names
    related = read(reader, "S/o Ekambar Zaveri")
    assert reader.lexicon.name_key("Ekambar") in related.relatives
    assert read(reader, "Guardian:\nWali").names == {wali}


def test_every_date_is_read(reader):
    found = read(reader, "DOB: 02/01/1979 | admitted 03/03/2026 discharged 12/03/2026")
    assert len(found.dates) == 3 and 9 in found.stays


def test_conditions_are_read_by_name_and_optionally_by_code(invented, reader):
    assert read(reader, "essential medicines list").conditions == set()
    assert read(reader, "known Typhoid fever").conditions == {"A01.0"}
    assert read(reader, "K/C/O: I10").conditions == set()
    stronger = Attacker(invented.parents[1], invented, Settings(condition_codes=True))
    assert read(stronger, "K/C/O: I10").conditions == {"I10"}
    assert read(stronger, "टाइफाइड").conditions == {"A01.0"}


def test_only_patient_identifier_fields_join_records():
    rows = [["sample_id", "uhid", "patient_name"], ["S26000001", "HOSP-0000001", "x"],
            ["S26000001", "[ID]", "y"]]
    assert _references("row:0", rows) == ["ref:HOSP-0000001"]
    assert _references("row:1", rows) == []
    nested = {"patient": {"hospital_uhid": "HOSP-0000001"}, "visit_id": "OPD/26/01/00001"}
    assert _references("doc", nested) == ["ref:HOSP-0000001"]


def test_the_released_file_name_is_read_not_the_input_one(invented, reader, tmp_path):
    from utility import Release

    (invented.parents[1] / "input" / "arogya_ds.txt").write_text("seen today\n", "utf-8")
    (tmp_path / "sanitized").mkdir()
    (tmp_path / "sanitized" / "file_1.txt").write_text("seen today\n", "utf-8")
    (tmp_path / "report.json").write_text(json.dumps(
        {"files": [{"input_sha256": "s1", "output": "sanitized/file_1.txt"}]}), "utf-8")
    (tmp_path / "records.jsonl").write_text("", "utf-8")
    unit = {"patient": "P1", "entities": []}
    files = {"arogya_ds.txt": {"sha256": "s1", "format": "text", "units": {"doc": unit}}}
    for output, hospitals in ((None, {"Arogya Seva Sadan"}), (tmp_path, set())):
        release = Release(invented, output)
        release.truth = {**release.truth, "files": files}
        (found,) = reader.records(release)
        assert found["evidence"].hospitals == hospitals


def record(attacker, text, references=(), patient="P1"):
    return {"key": (text, "doc"), "patient": patient, "text": text,
            "references": list(references), "evidence": read(attacker, text)}


def test_a_record_is_as_exposed_as_its_profile(reader):
    records = [record(reader, "Mobile: 70000 00001", ["ref:1"]),
               record(reader, "Haemoglobin 11", ["ref:1"]),
               record(reader, "Haemoglobin 12"),
               record(reader, "ward census", patient=None)]
    profiles = reader.profiles(records)
    assert [p["members"] for p in profiles] == [[0, 1], [2]]
    found = reader.linkage(records, profiles)
    assert found["records_attacked"] == 3
    assert found["A2/A3"] == pytest.approx((1 + 1 + 0.5) / 3)
    assert found["linkage_record_only"] == pytest.approx((1 + 0.5 + 0.5) / 3)


def test_the_targeted_attack_says_which_signals_matched(reader):
    records = [record(reader, "Typhoid fever, seen on 04/03/2026"),
               record(reader, "seen on 04/03/2026", patient=None),
               record(reader, "Gender: F, Typhoid fever", ["ref:2"])]
    profiles = reader.profiles(records)
    trace: list = []
    success, _, _, hits = reader.targeted(profiles, trace=trace)
    (target,) = trace
    assert target["consistent"] == 2 and target["success"] == success
    assert hits == {"date": 1, "condition": 2}
    assert sorted(sorted(m["signals"]) for m in target["matches"]) \
        == [["condition"], ["condition", "date"]]
    assert reader.targeted(profiles, ignore=frozenset({"date", "condition"}))[0] == 0.0


# ------------------------------------------------------------------- calibration


@pytest.fixture(scope="module")
def calibration():
    from calibrate_attacker import compare, official

    attacker = Attacker(TOOLKIT, TRUTH)
    rows = compare(official(TRUTH), attacker.measure(attacker.raw))
    return {name: (want, got, verdict) for name, want, got, verdict in rows}


@kit
def test_the_replay_agrees_with_the_organisers_on_the_raw_input(calibration):
    """Every component that enters the score, and the targeted signal-hit counts."""
    off = {name: row for name, row in calibration.items() if row[2] == "OFF"}
    assert off == {}
    for name in ("record_linkage_risk", "patients_reidentified", "targeted_success"):
        want, got, _ = calibration[name]
        assert abs(got - want) <= 0.05, name
    for name in ("date", "condition", "stay"):
        want, got, _ = calibration[f"signal hits: {name}"]
        assert abs(got - want) <= 2, name
    # the same records, joined into the same number of profiles
    for name in ("records_attacked", "clusters", "targets"):
        assert calibration[name][0] == calibration[name][1], name


@kit
def test_the_unscored_readings_stay_close(calibration):
    """Their definitions are not published; ours are about 0.06-0.07 high (see the tool)."""
    for name in ("unique_records", "confident_record_reid"):
        want, got, _ = calibration[name]
        assert abs(got - want) <= 0.08, name


@pytest.mark.parametrize("text", ["DOB: 1987", "Date of birth DOB | 1987"])
def test_a_birth_year_released_alone_is_read(reader, text):
    assert read(reader, text).births == {1987}

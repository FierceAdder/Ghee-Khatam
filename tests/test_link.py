"""Linking rules: when records belong to one patient, and when they must stay apart."""

from __future__ import annotations

import re

from wards.link import names_compatible, parse_name

PSN = re.compile(r"^PSN-[0-9A-HJKMNP-TV-Z]{6}$")


def registry(*rows: str) -> dict:
    header = "UHID,Patient Name,Gender,Age,DOB,Relative Name,Village,District,Mobile,Reg Date\n"
    return {"registry.csv": header + "".join(row + "\n" for row in rows)}


def patients(release, files) -> list:
    result = release(files)
    return [result.claims().get(("registry.csv", f"row:{i}"))
            for i in range(files["registry.csv"].count("\n") - 1)]


def test_strong_identifier_links_across_files_and_scripts(release):
    result = release({
        "a.txt": "Patient Name: Wanrisha Kharbuli\nUHID: KMC-100417\n",
        "b.txt": "नाम: वानरिशा\nUHID: KMC-100417\n",
    })
    a, b = result.claims()[("a.txt", "doc")], result.claims()[("b.txt", "doc")]
    assert PSN.match(a) and a == b
    assert a in result.text("a.txt") and a in result.text("b.txt")


def test_homonyms_are_never_merged(release):
    got = patients(release, registry(
        "KMC-1001,Naveen Bajpai,M,45,,Shyam Bajpai,Bewar,Mainpuri,9812345670,01/02/2026",
        "KMC-2002,Naveen Bajpai,M,21,,Vijay Bajpai,Ghiror,Mainpuri,9012345678,01/02/2026",
    ))
    assert all(got) and got[0] != got[1]


def test_same_name_and_birth_date_links(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,12/04/1981,,,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,45,12/04/1981,,,Mainpuri,,03/02/2026",
    ))
    assert got[0] == got[1]


def test_same_name_and_relative_links(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,Mohan Verma,,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,45,,Mohan Verma,,Mainpuri,,03/02/2026",
    ))
    assert got[0] == got[1]


def test_name_village_and_close_age_links(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,,Bewar,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,46,,,Bewar,Mainpuri,,03/02/2026",
    ))
    assert got[0] == got[1]


def test_name_and_village_with_distant_ages_stays_apart(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,,Bewar,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,63,,,Bewar,Mainpuri,,03/02/2026",
    ))
    assert got[0] != got[1]


def test_a_name_alone_never_links(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,,,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,45,,,,Mainpuri,,03/02/2026",
    ))
    assert got[0] != got[1]


def test_ambiguous_clue_is_not_guessed(release):
    result = release({**registry(
        "KMC-1001,Asha Verma,F,45,12/04/1981,Mohan Verma,,Mainpuri,,01/02/2026",
        "XYZ-9009,Asha Verma,F,20,01/01/2006,Mohan Verma,,Mainpuri,,03/02/2026",
    ), "note.txt": "Asha, W/o Mohan, discharged.\n"})
    claims = result.claims()
    registered = {claims[("registry.csv", "row:0")], claims[("registry.csv", "row:1")]}
    assert len(registered) == 2
    assert claims[("note.txt", "doc")] not in registered


def test_fragmented_note_links_through_first_name_and_relative(release):
    result = release({**registry(
        "KMC-1001,Asha Verma,F,45,,Mohan Verma,,Mainpuri,,01/02/2026"),
        "note.txt": "Asha, W/o Mohan, discharged in stable condition.\n"})
    claims = result.claims()
    assert claims[("note.txt", "doc")] == claims[("registry.csv", "row:0")]


def test_first_name_alone_is_not_linked(release):
    result = release({"note.txt": "Kusum ji ka sugar control mein hai.\n"})
    assert result.claims()[("note.txt", "doc")] is None
    assert "Kusum" not in result.text("note.txt")


def test_shared_family_phone_does_not_merge_two_people(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,,,Mainpuri,9812345670,01/02/2026",
        "KMC-1002,Mohan Verma,M,48,,,,Mainpuri,9812345670,01/02/2026",
    ))
    assert all(got) and got[0] != got[1]


def test_hub_phone_links_nobody(release):
    got = patients(release, registry(
        "KMC-1001,Asha Verma,F,45,,,,Mainpuri,9812345670,01/02/2026",
        "KMC-1002,Mohan Gupta,M,48,,,,Mainpuri,9812345670,01/02/2026",
        "KMC-1003,Sunita Yadav,F,30,,,,Mainpuri,9812345670,01/02/2026",
    ))
    assert len(set(got)) == 3


def test_conflicting_aadhaar_blocks_a_merge(release):
    result = release({
        "a.txt": "Name: Asha Verma\nAadhaar: 4821 7365 9012\nMob: 9812345670\n",
        "b.txt": "Name: Asha Verma\nAadhaar: 7391 4628 5510\nMob: 9812345670\n",
    })
    assert result.claims()[("a.txt", "doc")] != result.claims()[("b.txt", "doc")]


def test_document_about_several_people_is_not_linked(release):
    result = release({
        "list.txt": "Name: Asha Verma\nName: Mohan Gupta\nName: Sunita Yadav\n",
        "a.txt": "Name: Asha Verma\nUHID: KMC-100417\n",
    })
    assert result.claims()[("list.txt", "doc")] is None


def test_phone_only_record_anchors_nobody(release):
    result = release({"log.csv": "trip,remarks\nT1,called by Temsula Ozukum 9812345670\n"})
    assert result.claims()[("log.csv", "row:0")] is None
    assert "Temsula" not in result.everything()


def test_split_name_columns_are_one_person(release):
    result = release({
        "legacy.csv": "PatientID,LastName,FirstName,Sex\nKMC-1001,Verma,Asha,F\n",
        "note.txt": "Patient Name: Asha Verma\nUHID: KMC-1001\n",
    })
    claims = result.claims()
    assert claims[("legacy.csv", "row:0")] == claims[("note.txt", "doc")] is not None


def test_pseudonyms_change_with_the_secret(release):
    files = {"a.txt": "Name: Asha Verma\nUHID: KMC-100417\n"}
    one = release(files, secret=b"first").claims()[("a.txt", "doc")]
    two = release(files, secret=b"second").claims()[("a.txt", "doc")]
    again = release(files, secret=b"first").claims()[("a.txt", "doc")]
    assert one != two and one == again
    assert "100417" not in one


def test_name_compatibility(lexicon):
    def name(value):
        return parse_name(value, lexicon)

    assert names_compatible(name("Mohammad Faizan"), name("Faizan sahab"))
    assert names_compatible(name("Anju Tomar"), name("TOMAR ANJU"))
    assert names_compatible(name("कुसुम बघेल"), name("Kusum Baghel"))
    assert not names_compatible(name("Naveen Tripathi"), name("Girish Tripathi"))
    assert not names_compatible(name("Mohammad Arif"), name("Mohammad Faizan"))
    assert parse_name("Dr ji", lexicon) is None

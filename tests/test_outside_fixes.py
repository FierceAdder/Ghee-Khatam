"""Fixes for defects an outside set showed, each reproduced with invented data."""

from __future__ import annotations

import re

import pytest

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

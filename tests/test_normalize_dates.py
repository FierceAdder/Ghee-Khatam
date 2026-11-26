"""Cross-script name matching, date parsing and the privacy transforms."""

from __future__ import annotations

import datetime as dt
from collections import Counter

import pytest

from wards import dates
from wards.normalize import phone_key, phonetic_key, transliterate
from wards.privacy import Privacy
from wards.security import Secret

# ---------------------------------------------------------------------- names


@pytest.mark.parametrize("a, b", [
    ("Kamla", "Kamala"), ("Reena", "Rina"), ("Chauhan", "Chouhan"),
    ("Parvati", "Parvathi"), ("Siddiqui", "Siddiqi"), ("Sukhwinder", "Sukhvinder"),
    ("Pandey", "पांडे"), ("Singh", "सिंह"), ("Meenakshi", "मीनाक्षी"), ("Asha", "आशा"),
    ("Qureshi", "क़ुरैशी"), ("Yusuf", "यूसुफ"), ("Zainab", "Zenab"),
])
def test_spelling_variants_share_a_key(lexicon, a, b):
    assert lexicon.name_key(a) == lexicon.name_key(b)


@pytest.mark.parametrize("a, b", [
    ("Kamal", "Kamla"), ("Rina", "Rani"), ("Sunil", "Sunita"), ("Ramesh", "Rakesh"),
    ("Manoj", "Mohan"), ("Anil", "Anita"),
])
def test_different_names_keep_different_keys(lexicon, a, b):
    assert lexicon.name_key(a) != lexicon.name_key(b)


@pytest.mark.parametrize("variant", ["Md", "Md.", "Mohd.", "Muhammad", "Mohammed", "मोहम्मद"])
def test_common_abbreviation_variants(lexicon, variant):
    assert lexicon.name_key(variant) == lexicon.name_key("Mohammad")


def test_final_schwa_is_silent():
    assert transliterate("कमल") == "kamal"
    assert transliterate("कमला") == "kamalaa"


def test_empty_token_has_no_key():
    assert phonetic_key("123") == ""


def test_phone_key_ignores_prefixes():
    assert phone_key("+91 98123-45670") == phone_key("098123 45670") == "9812345670"


# ---------------------------------------------------------------------- dates


def only(text: str, **kwargs) -> dates.DateMatch:
    found = dates.find_dates(text, **kwargs)
    assert len(found) == 1, [d.text for d in found]
    return found[0]


@pytest.mark.parametrize("text, order, expected, shifted", [
    ("12/04/1977", "dmy", dt.date(1977, 4, 12), "22/04/1977"),
    ("04/12/1977", "mdy", dt.date(1977, 4, 12), "04/22/1977"),
    ("2026-05-20", "dmy", dt.date(2026, 5, 20), "2026-05-30"),
    ("2026/05/20", "dmy", dt.date(2026, 5, 20), "2026/05/30"),
    ("26.10.2026", "dmy", dt.date(2026, 10, 26), "05.11.2026"),
    ("5/9/2026", "dmy", dt.date(2026, 9, 5), "15/9/2026"),
    ("12-Apr-1977", "dmy", dt.date(1977, 4, 12), "22-Apr-1977"),
    ("3rd March 2026", "dmy", dt.date(2026, 3, 3), "13th March 2026"),
    ("March 3, 2026", "dmy", dt.date(2026, 3, 3), "March 13, 2026"),
    ("25 DEC 2026", "dmy", dt.date(2026, 12, 25), "04 JAN 2027"),
    ("3 मार्च 2026", "dmy", dt.date(2026, 3, 3), "13 मार्च 2026"),
])
def test_dates_are_read_and_written_in_their_own_format(text, order, expected, shifted):
    from wards.textview import TextView

    view = TextView.of(text).text
    match = only(view)
    assert match.resolve(order) == expected
    assert match.render(expected + dt.timedelta(days=10), order) == TextView.of(shifted).text


def test_iso_timestamp_keeps_its_time():
    assert only("2026-10-23T09:30:00+05:30").text == "2026-10-23"


def test_chat_stamp_with_short_year():
    match = only("[30/05/26, 9:32 pm] Someone:")
    assert match.resolve("dmy") == dt.date(2026, 5, 30)


def test_yearless_date():
    match = only("Visit: 5 September")
    assert match.resolve("dmy", 2026) == dt.date(2026, 9, 5)
    assert match.render(dt.date(2026, 10, 1), "dmy") == "1 October"


@pytest.mark.parametrize("text", [
    "1-0-1", "BP 123/76", "Range 12.0-16.0", "OPD/26/05/99999", "IPD-2026-00454",
    "91-1111-2222-3333", "version 3.1", "week 2026-W44", "Platelet 150000-410000",
    "he may 5 times", "Dose 2 may be repeated", "31/31/2026",
])
def test_non_dates_are_left_alone(text):
    assert dates.find_dates(text) == []


def test_compact_dates_only_in_typed_fields():
    assert dates.find_dates("20260301") == []
    assert only("20260301", typed=True).resolve("dmy") == dt.date(2026, 3, 1)


def test_order_comes_from_unambiguous_dates():
    found = dates.find_dates("01/13/2026 then 02/01/2026")
    assert dates.choose_order(found, None, Counter()) == "mdy"


def test_ambiguous_file_follows_its_siblings():
    found = dates.find_dates("02/01/2026")
    assert dates.choose_order(found, None, Counter(mdy=3, dmy=1)) == "mdy"


def test_ambiguous_pair_prefers_the_plausible_stay():
    found = dates.find_dates("Admission 02/01/2026 Discharge 02/06/2026")
    assert dates.choose_order(found, None, Counter()) == "mdy"


def test_header_hint_wins():
    assert dates.order_hint("DOB (MM/DD/YYYY)") == "mdy"
    assert dates.order_hint("DOB (DD-MM-YYYY)") == "dmy"
    assert dates.order_hint("DOB") is None


def test_default_order_is_day_first():
    assert dates.choose_order(dates.find_dates("02/01/2026"), None, Counter()) == "dmy"


# -------------------------------------------------------------------- privacy


@pytest.fixture
def privacy(policy):
    return Privacy(policy, Secret(b"k"))


def test_shift_is_bounded_and_never_zero(privacy, policy):
    offsets = [privacy.shift_days(f"patient-{i}") for i in range(2000)]
    limit = policy.date_shift_max_days
    assert all(-limit <= o <= limit and o != 0 for o in offsets)
    assert min(offsets) < -limit * 0.9 and max(offsets) > limit * 0.9


def test_shift_depends_on_the_secret(policy):
    a = [Privacy(policy, Secret(b"a")).shift_days(str(i)) for i in range(50)]
    b = [Privacy(policy, Secret(b"b")).shift_days(str(i)) for i in range(50)]
    assert a != b


def test_stay_length_is_exact(privacy):
    admitted, discharged = only("24/01/2026"), only("02/02/2026")
    offset = privacy.shift_days("PSN-TEST")
    first = only(privacy.shifted(admitted, "dmy", offset, 2026))
    last = only(privacy.shifted(discharged, "dmy", offset, 2026))
    assert (last.resolve("dmy") - first.resolve("dmy")).days == 9


@pytest.mark.parametrize("age", range(0, 121))
def test_age_band_always_contains_the_age(privacy, policy, age):
    band = privacy.age_band(age)
    if band.endswith("+"):
        assert age >= int(band[:-1]) == policy.age_top_code
    else:
        low, high = map(int, band.split("-"))
        assert low <= age <= high and high - low < policy.age_band_years


def test_pin_prefix(privacy):
    assert privacy.pin_prefix("123456") == "123***"
    assert privacy.pin_prefix("123 456") == "123***"
    assert privacy.pin_prefix("12") == "[PIN]"
    assert privacy.pin_number("123456") == "123"


def test_shipped_policy_removes_birth_dates(privacy):
    assert privacy.date_of_birth(only("01/01/1990"), "dmy", 5, 2026, False) is None


def test_birth_date_removed_for_the_oldest(policy):
    from dataclasses import replace

    privacy = Privacy(replace(policy, dob_mode="shift"), Secret(b"k"))
    old = only("01/01/1930")
    assert privacy.date_of_birth(old, "dmy", 5, 2026, top_coded=False) is None
    young = only("01/01/1990")
    assert privacy.date_of_birth(young, "dmy", 5, 2026, top_coded=False) == "06/01/1990"
    assert privacy.date_of_birth(young, "dmy", 5, 2026, top_coded=True) is None


@pytest.mark.parametrize("mode, expected", [
    ("shift", "06/01/1990"), ("year", "1990"), ("remove", None),
])
def test_birth_date_modes(policy, mode, expected):
    from dataclasses import replace

    privacy = Privacy(replace(policy, dob_mode=mode), Secret(b"k"))
    assert privacy.date_of_birth(only("01/01/1990"), "dmy", 5, 2026, False) == expected

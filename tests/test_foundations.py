"""Text views, JSON round trips, policy, secrets and safe logging."""

from __future__ import annotations

import io

import pytest

from wards import jsonio
from wards.errors import ConfigError
from wards.policy import Policy, load_policy
from wards.security import SafeLog, Secret
from wards.textview import TextView

# ------------------------------------------------------------------ text view


def test_plain_ascii_view_is_the_identity():
    view = TextView.of("Mob 9812345670")
    assert view.text == view.source
    assert view.source_span(4, 14) == (4, 14)


@pytest.mark.parametrize("disguised, plain", [
    ("Kal​avati", "Kalavati"),                       # zero-width space
    ("९८१२३४५६७०", "9812345670"),                         # Devanagari digits
    ("Ｋａｌａ", "Kala"),                                    # full width (NFKC)
    ("Kаlа", "Kala"),                           # Cyrillic homoglyph
    ("\\u0930\\u093e\\u092e", "राम"),                     # literal \u escapes
    ("&#82;&#97;&#109;", "Ram"),                          # decimal entities
    ("Ram%20Lal", "Ram Lal"),                             # percent encoding
    ("%E0%A4%B0%E0%A4%BE%E0%A4%AE", "राम"),
])
def test_disguises_are_undone(disguised, plain):
    assert TextView.of(disguised).text == plain


def test_view_spans_map_back_to_the_original():
    source = "id &#82;&#97;&#109; ok"
    view = TextView.of(source)
    start = view.text.index("Ram")
    assert view.source_text(start, start + 3) == "&#82;&#97;&#109;"


def test_zero_width_characters_stay_inside_the_mapped_span():
    source = "x Kal​avati y"
    view = TextView.of(source)
    start = view.text.index("Kalavati")
    assert view.source_text(start, start + len("Kalavati")) == "Kal​avati"


def test_surrogate_pairs_decode_to_one_character():
    view = TextView.of("a \\ud83d\\ude4f b")
    assert view.text == "a 🙏 b"


def test_percentages_are_not_treated_as_encoding():
    assert TextView.of("SpO2 97%. HbA1c 8.8 %,").text == "SpO2 97%. HbA1c 8.8 %,"


def test_invalid_span_is_rejected():
    with pytest.raises(ValueError):
        TextView.of("abc").source_span(2, 2)


# ----------------------------------------------------------------------- JSON


def test_number_literals_survive_a_round_trip():
    text = '{"a":0.80,"b":1.50e3,"c":10,"d":-0.0,"e":[true,false,null]}'
    assert jsonio.dumps(jsonio.loads(text), spaced=False) == text


def test_indented_layout_is_preserved():
    text = '{\n    "a": [\n        1,\n        {}\n    ]\n}'
    indent, spaced = jsonio.detect_style(text)
    assert indent == "    "
    assert jsonio.dumps(jsonio.loads(text), indent=indent, spaced=spaced) == text


def test_lone_surrogates_cannot_break_the_writer():
    out = jsonio.dumps(jsonio.loads('"a\\ud800b"'))
    out.encode("utf-8")
    assert out == '"a�b"'


def test_depth_ignores_brackets_inside_strings():
    assert jsonio.depth('{"a":"[[[[","b":[[1]]}') == 3


# --------------------------------------------------------------------- policy


def test_shipped_policy_loads(policy):
    assert policy.age_band_years == 5 and policy.date_shift_max_days == 180
    assert policy.dob_mode == "remove"


@pytest.mark.parametrize("content, code", [
    ("age_band_years: 0", "policy_value_out_of_range.age_band_years"),
    ("date_shift_max_days: 400", "policy_value_out_of_range.date_shift_max_days"),
    ("dob_mode: keep", "policy_value_out_of_range.dob_mode"),
    ("age_band_year: 5", "policy_unknown_key"),
    ("age_band_years: five", "policy_value_not_integer.age_band_years"),
    ("just a line", "policy_line_without_colon"),
])
def test_bad_policy_stops_the_run(tmp_path, content, code):
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=code):
        load_policy(path)


def test_missing_policy_stops_the_run(tmp_path):
    with pytest.raises(ConfigError):
        load_policy(tmp_path / "absent.yaml")


def test_default_policy_is_valid():
    Policy().validate()


# ------------------------------------------------------------------- security


def test_missing_secret_is_refused():
    with pytest.raises(ConfigError, match="raksha_secret_not_set"):
        Secret.from_env({})


def test_secret_never_shows_itself():
    secret = Secret(b"do-not-print-me")
    for rendered in (repr(secret), str(secret), f"{secret}"):
        assert "do-not-print-me" not in rendered
    with pytest.raises(TypeError):
        secret.__reduce__()


def test_mac_is_keyed_and_domain_separated():
    one, two = Secret(b"one"), Secret(b"two")
    assert one.mac("a", "x") != two.mac("a", "x")
    assert one.mac("a", "x") != one.mac("b", "x")
    assert one.mac("a", "x") == Secret(b"one").mac("a", "x")


def test_log_refuses_text_values():
    log = SafeLog(io.StringIO())
    with pytest.raises(TypeError):
        log.event("done", files="Kalavati Devi")


def test_log_redacts_anything_that_is_not_a_fixed_code():
    stream = io.StringIO()
    log = SafeLog(stream)
    log.problem("parse_failed", "ab" * 32, ValueError("Kalavati 9812345670"))
    log.problem("Kalavati Devi 9812345670", "not-hex!", reason="phone 9812345670")
    out = stream.getvalue()
    assert "Kalavati" not in out and "9812345670" not in out
    assert "error=ValueError" in out and "file=abababababab" in out

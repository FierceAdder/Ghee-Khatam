"""Phase 14 options: the learned name check, Mondrian ages, l-diversity, ICD-10 rollup and
fuzzy spellings. All names and numbers here are invented."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from wards.clinical import generalise
from wards.link import jaro_winkler
from wards.model import FieldType as F
from wards.model import Segment
from wards.namemodel import Likeness, NameModel, Perceptron, Stats, ask, words
from wards.privacy import Privacy
from wards.security import Secret

# ---------------------------------------------------------------- name model


def test_words_read_possessives_as_the_rules_do():
    assert [w.text for w in words("Asha's report, Meren’s BP")] == ["Asha", "report", "Meren",
                                                                    "BP"]


@pytest.mark.parametrize("word, asked", [
    ("Lalthanzuala", True), ("HTN", False), ("DM2", False), ("TB", False), ("of", False),
    ("Paracetamol", False), ("x", False)])
def test_only_plausible_words_are_asked_about(lexicon, word, asked):
    assert ask(lexicon, word, covered=False) is asked
    assert ask(lexicon, word, covered=True) is False


def test_name_likeness_prefers_names():
    likeness = Likeness.train(["Lalremruata", "Vanlalruata", "Zothansangi", "Lalthanzuala"],
                              ["haemoglobin", "creatinine", "discharge", "treatment"])
    assert likeness.score("Lalrinpuia") > likeness.score("hemoglobin")


def test_a_trained_model_adds_names_and_nothing_else(lexicon, tmp_path):
    """A perceptron that says yes to any capitalised word of unknown shape: detections are
    added only for words no rule covered, as one run per name."""
    (tmp_path / "name_perceptron.json").write_text(json.dumps(
        {"threshold": 0.5, "weights": {"shape=title": 1.0}}))
    model = NameModel("perceptron", lexicon, tmp_path)
    segment = Segment("Seen today: Lalthanzuala Ralte, stable.", field=F.TEXT)
    found = model.mentions(segment, Stats())
    assert [m.value for m in found] == ["Lalthanzuala Ralte"]     # "Seen" is an ordinary word
    assert all(m.source == "model" for m in found)


def test_a_failing_model_never_costs_the_file(release, monkeypatch):
    from wards import pipeline as pipeline_module

    class Broken:
        def document(self, segments, stats):
            raise RuntimeError("model crashed")

    monkeypatch.setattr(pipeline_module, "_load_name_model", lambda backend, lexicon: Broken())
    out = release({"note.txt": "Patient Name: Meren Longkumer\nBP 120/80\n"},
                  name_model="perceptron")
    assert "Meren" not in out.text("note.txt")
    assert out.report["counts"]["files_quarantined"] == 0


def test_perceptron_scores_add_up():
    model = Perceptron({"a": 1.0, "b": -0.4}, threshold=0.5)
    assert model.predict_line([["a"], ["a", "b"], ["b"]]) == [True, True, False]


# --------------------------------------------------------------- age ranges


@pytest.fixture
def mondrian(policy) -> Privacy:
    return Privacy(replace(policy, age_partitioning="mondrian"), Secret(b"k"))


def test_mondrian_ranges_hold_the_age_and_enough_people(mondrian):
    ages = [23, 24, 25, 31, 33, 40, 41, 58, 62, 63]
    patients = [(f"p{i}", "Sitapur", "F", age) for i, age in enumerate(ages)]
    mondrian.partition(patients)
    k = -(-mondrian.policy.min_group_size // mondrian.policy.registry_per_patient)
    groups: dict = {}
    for key, _, _, age in patients:
        low, high = mondrian.ranges[key]
        assert low <= age <= high
        assert mondrian.age_band(age, key) == f"{low}-{high}"
        groups.setdefault((low, high), []).append(key)
    assert all(len(members) >= k for members in groups.values())
    assert len(groups) > 1


def test_mondrian_leaves_a_lone_patient_on_the_global_band(mondrian):
    mondrian.partition([("p1", "Sitapur", "F", 47)])
    assert "p1" not in mondrian.ranges
    assert mondrian.age_band(47, "p1") == mondrian.age_band(47)


def test_l_diversity_widens_bands_when_a_group_shares_one_condition(policy):
    crowd = [("Sitapur", "F", 30 + i % 4, frozenset({"A90"})) for i in range(30)]
    plain = Privacy(replace(policy, l_diversity=0), Secret(b"k"))
    diverse = Privacy(replace(policy, l_diversity=2), Secret(b"k"))
    assert plain.choose_band_width(crowd) == policy.age_band_years
    assert diverse.choose_band_width(crowd) == policy.age_band_max_years


@pytest.mark.parametrize("code, mode, written", [
    ("B55.0", "suppress", "R69"), ("B55.0", "category", "B55"), ("B55.0", "chapter", "A00-B99"),
    ("G51.0", "chapter", "G00-G99"), ("I25.1", "chapter", "I00-I99")])
def test_icd10_rollup(code, mode, written):
    assert generalise(code, mode) == written


# ------------------------------------------------------------- fuzzy names


def test_jaro_winkler_reference_values():
    assert round(jaro_winkler("MARTHA", "MARHTA"), 3) == 0.961
    assert round(jaro_winkler("DIXON", "DICKSONX"), 3) == 0.813
    assert jaro_winkler("abc", "abc") == 1.0 and jaro_winkler("", "x") == 0.0


def test_fuzzy_spellings_join_variants_but_not_different_people(release):
    files = {"a.txt": "Patient Name: Hmingthansangahmi Ralte\nUHID QXR-0001\n",
             "b.txt": "Patient Name: Hmingthansanga Ralte\nS/o Lalbiakzuala Ralte\n"
                      "Age: 44\n",
             "c.txt": "Patient Name: Hmingthansangahmi Ralte\nS/o Lalbiakzuala Ralte\n"
                      "UHID QXR-0001\n",
             "d.txt": "Patient Name: Ramesh Ralte\nUHID QXR-0002\n",
             "e.txt": "Patient Name: Rajesh Ralte\nUHID QXR-0003\n"}
    out = release(files, name_matching="fuzzy")
    claims = out.claims()
    assert claims[("b.txt", "doc")] == claims[("c.txt", "doc")] is not None
    assert claims[("d.txt", "doc")] != claims[("e.txt", "doc")]

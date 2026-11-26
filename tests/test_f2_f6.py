"""Lab, vital-sign and role words that must stay, and small leaks that must go: relatives
after a kinship word, localities with no cue, villages inside one address cell and first
names in file names. All names and numbers here are invented."""

from __future__ import annotations

import json

import pytest

from wards.model import NAME_TYPES, Role, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.namemodel import ask, words

NAMES = tuple(NAME_TYPES)


def asked(lexicon, line: str) -> list[str]:
    return [w.text for w in words(line) if ask(lexicon, w.text, False, w.whole)]


def found(detector, text: str, *types, field=F.TEXT):
    return [m for m in detector.detect_segment(Segment(text, field=field))
            if not types or m.etype in types]


def values(detector, text: str, *types, field=F.TEXT):
    return [m.value for m in found(detector, text, *types, field=field)]


# ----------------------------------------------- lab and vital-sign words (the model)


@pytest.mark.parametrize("line", [
    "HbA1c 8.9 %", "SpO2 < 94%", "eGFR and pH today", "CD4 count, HCO3, PaO2",
    "CBNAAT TRUENAT MANTOUX", "Haematocrit, Procalcitonin, Reticulocyte, Typhidot",
    "Pulse Oximetry, Urine Output, Glasgow Coma Scale", "हीमोग्लोबिन क्रिएटिनिन",
])
def test_lab_and_vital_words_are_never_asked_about(lexicon, line):
    assert asked(lexicon, line) == []


@pytest.mark.parametrize("line, name", [
    ("lalrinpuia HbA1c 8.9 %", "lalrinpuia"),
    ("zothanmawii SpO2 < 94%", "zothanmawii"),
    ("temsutula ko CBNAAT", "temsutula"),
    ("Lalthanzuala eGFR", "Lalthanzuala"),
])
def test_names_in_the_same_sentences_are_still_asked_about(lexicon, line, name):
    assert asked(lexicon, line) == [name]


def test_the_whole_token_is_kept_with_every_piece():
    assert [(w.text, w.whole) for w in words("HbA1c ok")] == \
        [("HbA", "HbA1c"), ("c", "HbA1c"), ("ok", "ok")]


def test_new_lab_words_are_nobodys_name(lexicon):
    new = ("haematocrit procalcitonin reticulocyte typhidot truenat cbnaat oximetry "
           "ferritin troponin fibrinogen glasgow homocysteine haemogram urinalysis").split()
    for word in new:
        assert lexicon.is_medical(word), word
    assert not lexicon.medical & (lexicon.first_names | lexicon.surnames)


PROTOCOL = """Diabetes and oxygen protocol, issued 12/03/2025.
HbA1c 8.9 % needs review in clinic.
SpO2 < 94% needs oxygen therapy.
Send CBNAAT and Haematocrit with eGFR before the visit.
Check Procalcitonin and Reticulocyte count.
Ask the Lab Karmachari to label tubes.
Typhoid fever (A01.0) and Visceral leishmaniasis (B55.0) follow this sheet.
"""


def test_a_protocol_keeps_its_lab_words_with_the_model_on(release):
    pytest.importorskip("onnxruntime")
    out = release({"protocol.txt": PROTOCOL}, name_model="indicbert")
    assert out.text("protocol.txt") == PROTOCOL


@pytest.mark.parametrize("line", [
    "lalrinpuia HbA1c 8.9 %, review in clinic.",
    "lalrinpuia SpO2 < 94% on room air.",
])
def test_a_lower_case_name_in_the_same_shape_is_removed(release, line):
    pytest.importorskip("onnxruntime")
    out = release({"protocol.txt": line + "\n"}, name_model="indicbert")
    text = out.text("protocol.txt")
    assert "lalrinpuia" not in text
    assert text == line.replace("lalrinpuia", "[NAME]") + "\n"


# ------------------------------------------------------------- hospital role words


@pytest.mark.parametrize("text", [
    "Sample received by Lab Karmachari.",
    "Sample received by Lab Assistant.",
    "Informant: Ward Attendant",
    "Attendant: Lab Assistant",
    "Form submitted by Ward Boy",
    "Report collected by Office Karmachari",
    "brought by Ward Attendant",
    "सूचना: लैब कर्मचारी",
    "Attendant name: Staff Sister",
])
def test_role_words_are_never_a_person(detector, text):
    assert values(detector, text, *NAMES) == []


@pytest.mark.parametrize("text, name", [
    ("Sample received by Lab Karmachari Lalrinpuia", "Lalrinpuia"),
    ("Ward Attendant: Lalrinpuia", "Lalrinpuia"),
    ("Lab Assistant Lalrinpuia drew the sample", "Lalrinpuia"),
    ("Sister Temsutula noted vitals.", "Temsutula"),
    ("Ward sister Temsutula gave the dose.", "Temsutula"),
    ("duty sister temsutula was informed", "temsutula"),
    ("Informed ward sister Temsutula", "Temsutula"),
])
def test_a_name_beside_a_hospital_role_is_staff_not_a_relative(detector, text, name):
    mine = [m for m in found(detector, text, *NAMES) if name in m.value]
    assert mine and all(m.role is Role.STAFF for m in mine)


@pytest.mark.parametrize("text, name", [
    ("Her sister Temsutula signed the form.", "Temsutula"),
    ("her sister temsutula came", "temsutula"),
    ("Patient's Sister Temsutula is present.", "Temsutula"),
    ("meri sister temsutula ko bulaya", "temsutula"),
    ("Attendant: Temsutula", "Temsutula"),
])
def test_a_patients_own_sister_or_attendant_is_a_relative(detector, text, name):
    mine = [m for m in found(detector, text, *NAMES) if name in m.value]
    assert mine and all(m.role is Role.RELATIVE for m in mine)


# ---------------------------------------------- relatives after a kinship word


@pytest.mark.parametrize("text, name", [
    ("beta lalrinpuia ko bulaya gaya", "lalrinpuia"),
    ("bahu zothansangi saath aayi thi", "zothansangi"),
    ("Uska pati lalthanzuala, mobile band hai", "lalthanzuala"),
    ("chacha vanlalruata ne bataya", "vanlalruata"),
    ("his son lalremruata came today", "lalremruata"),
    ("daughter zothansangi informed", "zothansangi"),
    ("beti temsutula.", "temsutula"),
    ("bhai: lalrinpuia", "lalrinpuia"),
    ("बेटा लालरिनपुइया को बुलाया", "लालरिनपुइया"),
    ("Beta Lalrinpuia aaya", "Lalrinpuia"),
    ("pati lalthanzuala ka mobile band", "lalthanzuala"),
    ("bhai vanlalruata ke saath", "vanlalruata"),
])
def test_the_word_after_a_kinship_word_is_a_relative(bare_lexicon, text, name):
    from wards.detect import Detector

    mine = [m for m in found(Detector(bare_lexicon), text, *NAMES) if name in m.value]
    assert [m.value for m in mine] == [name]
    assert mine[0].role is Role.RELATIVE


@pytest.mark.parametrize("text", [
    "beta ko bukhar hai", "bhai bp normal hai", "son has fever", "beta bimar hai",
    "beta chhota hai", "pati sharab peeta hai", "beta blocker started",
    "beta HCG positive", "daughter alive and healthy", "father expired in 2019",
    "mother diabetic, brother healthy.", "wife pregnant, husband abroad.",
    "beta school se aaya", "bhai sahab ne bataya", "bahu ghar par hai", "beta thik hai.",
    "bhai dekho, aaram karo", "son doing well", "daughter crying.", "pati bahar kaam karta hai",
    "बेटा बीमार है", "पति शराब पीता है", "भाई को बुखार है", "Mother Alive, Father Expired",
    "has one son, healthy", "beta 5 saal ka hai", "beti padhai karti hai",
    "beta paani ki kami", "pati ration ka kaam karta hai", "bahu garbh se hai",
    "beti ghabrahat ki shikayat", "mother breastfeeding and well", "beta lactam allergy",
])
def test_ordinary_sentences_about_family_name_nobody(bare_lexicon, text):
    from wards.detect import Detector

    assert values(Detector(bare_lexicon), text, *NAMES) == []


def test_a_lower_case_relative_leaves_the_release(release):
    out = release({"note.txt": "Patient Name: Meren Longkumer\n"
                               "beta lalrinpuia ko bulaya gaya.\nbeta ko bukhar hai.\n"},
                  name_model="off")
    text = out.text("note.txt")
    assert "lalrinpuia" not in text and "[RELATIVE]" in text
    assert "beta ko bukhar hai." in text


# ------------------------------------------------------------------ addresses


@pytest.mark.parametrize("cell, gone", [
    ("H.No. 12, Kamla Residency Phase Two Behind The Old Water Works, Sitapur",
     "Kamla Residency Phase Two Behind The Old Water Works"),
    ("C/O Meren Longkumer H.No. 12 Vallabh Nagar", "Vallabh Nagar"),
    ("Sitapur - 261001 Vallabh Kunj", "Vallabh Kunj"),
    ("District Sitapur Vallabh Kunj", "Vallabh Kunj"),
])
def test_an_address_field_keeps_only_what_is_known_to_be_public(detector, cell, gone):
    hidden = values(detector, cell, E.LOCALITY, E.VILLAGE, field=F.ADDRESS)
    assert any(gone in value for value in hidden), hidden


@pytest.mark.parametrize("cell", [
    "Sitapur, Uttar Pradesh", "Distt. Sitapur, UP, India", "Sitapur UP 261***",
    "District Sitapur Uttar Pradesh", "Patna, Bihar, PIN 800001", "लखनऊ, उत्तर प्रदेश",
    "Sitapur, 261xxx, Same as above",
])
def test_public_parts_of_an_address_are_kept(detector, cell):
    hidden = values(detector, cell, E.LOCALITY, E.VILLAGE, field=F.ADDRESS)
    assert [v for v in hidden if v != "Patna"] == []


@pytest.mark.parametrize("text, gone", [
    ("Resides at H.No. 12, Vallabh Nagar since May.", "Vallabh Nagar"),
    ("Shifted to H.No. 7, Sohanpura Colony yesterday.", "Sohanpura Colony"),
    ("Flat No. 3, Kamla Residency, Vallabh Marg", "Vallabh Marg"),
    ("H.No 5, Sector 62, Sitapur", "Sector 62"),
    ("मकान नं 5, वल्लभ नगर में रहते हैं", "वल्लभ नगर"),
])
def test_a_locality_after_a_house_number_in_prose_goes(detector, text, gone):
    assert any(gone in value for value in values(detector, text, E.LOCALITY))


@pytest.mark.parametrize("text", [
    "H.No. 12, Patient shifted to Medicine ward.",
    "H.No. 12, Blood Pressure 120/80 recorded.",
    "H.No. 12. Review after one week.",
    "H.No. 12, Road Traffic Accident reported.",
])
def test_words_after_a_house_number_that_are_no_place_stay(detector, text):
    assert values(detector, text, E.LOCALITY, E.VILLAGE) == []


ADDRESSES = ('UHID,Patient Name,Address\n'
             'QXR/26/004411,Meren Longkumer,"H.No. 4, Gandhi Colony, Sohanpura Nagar, '
             'Tehsil Biswan, Distt. Sitapur"\n'
             'QXR/26/004412,Temsu Jamir,"H.No. 9, Station Road, Kheiri Tola, Sitapur, UP"\n')


def test_the_village_of_a_combined_address_is_removed_from_other_files(release):
    out = release({
        "reg.csv": ADDRESSES,
        "note.txt": "UHID: QXR/26/004411\nCame from Sohanpura Nagar by bus.\nBP 120/80\n",
        "note2.txt": "UHID: QXR/26/004412\nवापस Kheiri Tola गए. Main Road accident.\n",
    }, name_model="off")
    assert "Sohanpura" not in out.text("note.txt") and "BP 120/80" in out.text("note.txt")
    assert "Kheiri" not in out.text("note2.txt")
    assert "Main Road accident." in out.text("note2.txt")
    assert "Distt. Sitapur" in out.text("reg.csv")


@pytest.mark.parametrize("header", ["Village/Mohalla", "Mohalla", "Address"])
def test_a_village_inside_one_cell_is_found_in_other_files(release, header):
    out = release({
        "reg.csv": f'UHID,Patient Name,{header}\n'
                   'QXR/26/004411,Meren Longkumer,"Gandhi Tola, Sohanpura Bazar, Sitapur"\n',
        "note.txt": "UHID: QXR/26/004411\nCame from Sohanpura Bazar by bus.\nBP 120/80\n",
    }, name_model="off")
    assert "Sohanpura" not in out.text("note.txt") and "BP 120/80" in out.text("note.txt")


@pytest.mark.parametrize("cell, learned", [
    ("H.No. 4, Gandhi Colony, Sohanpura Nagar, Tehsil Biswan, Distt. Sitapur",
     "Sohanpura Nagar"),
    ("Kheiri Tola, Sitapur, Uttar Pradesh 261001", "Kheiri Tola"),
])
def test_only_the_last_private_part_is_learned_as_a_village(lexicon, detector, cell, learned):
    from wards.knowledge import Knowledge

    segment = Segment(cell, field=F.ADDRESS)
    segment.mentions = detector.detect_segment(segment)
    knowledge = Knowledge(lexicon)
    for mention in segment.mentions:
        knowledge._learn_mention(mention)
    assert learned.casefold() in knowledge.villages
    assert "gandhi colony" not in knowledge.villages


@pytest.mark.parametrize("cell", ["H.No. 4, Main Road, Sitapur", "New Colony, Sitapur",
                                  "Sector 4, Sitapur, UP", "Vallabh Tola, Dr. Meren Ao"])
def test_a_general_place_phrase_is_not_learned(lexicon, detector, cell):
    from wards.knowledge import Knowledge

    knowledge = Knowledge(lexicon)
    for mention in found(detector, cell, field=F.ADDRESS):
        knowledge._learn_mention(mention)
    assert knowledge.villages == {}


# -------------------------------------------------------------------- file names


def test_a_patients_name_parts_are_removed_from_file_names(release):
    registry = ("UHID,Patient Name\nQXR/26/004411,Lalrinpuia Hauzel\n"
                "QXR/26/004412,Temsutula Kumar\nQXR/26/004413,तेमजेन जमीर\n")
    names = ["ds_lalrinpuia.txt", "hauzel_cbc.txt", "rptTemsutula.txt", "DS_LALRINPUIA_2.txt",
             "ds_तेमजेन.txt", "xray-temjen.txt", "notes/temsutula/day.txt"]
    files = {"reg.csv": registry, **{name: f"BP 120/8{i}\n" for i, name in enumerate(names)}}
    out = release(files, name_model="off")
    released = "\n".join(entry["output"] for entry in out.report["files"]).casefold()
    for part in ("lalrinpuia", "hauzel", "temsutula", "तेमजेन", "temjen"):
        assert part not in released, part
    assert out.report["counts"]["files_quarantined"] == 0


def test_generic_medical_and_ordinary_words_stay_in_file_names(release):
    registry = ("UHID,Patient Name\nQXR/26/004411,Lalrinpuia Kumar\n"
                "QXR/26/004412,Parkinson Devi\n")
    names = ["kumar_protocol.txt", "devi_list.txt", "parkinson_protocol.txt",
             "ds_summary_fever.txt", "sugar_chart.txt", "lalrin_ward.txt"]
    files = {"reg.csv": registry, **{name: f"BP 120/8{i}\n" for i, name in enumerate(names)}}
    out = release(files, name_model="off")
    released = {entry["output"] for entry in out.report["files"]}
    assert released >= {f"sanitized/{name}" for name in names}


def test_file_names_are_the_same_on_every_run(release):
    files = {"reg.csv": "UHID,Patient Name\nQXR/26/004411,Lalrinpuia Hauzel\n",
             "ds_lalrinpuia.txt": "BP 120/80\n"}
    first, second = release(files), release(files)
    assert json.dumps(first.report["files"], sort_keys=True) == \
        json.dumps(second.report["files"], sort_keys=True)

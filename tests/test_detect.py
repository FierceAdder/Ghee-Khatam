"""Detection: identifiers, names in three scripts, addresses, ages and the medical veto."""

from __future__ import annotations

import base64

import pytest

from wards.detect import Detector
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.model import Role, Segment


def found(detector: Detector, text: str, field: F = F.TEXT) -> list[tuple[E, str]]:
    segment = Segment(text, field=field)
    return [(m.etype, m.value) for m in detector.detect_segment(segment)]


def values(detector, text, *types, field=F.TEXT) -> list[str]:
    return [v for t, v in found(detector, text, field) if t in types]


NAMES = (E.PATIENT_NAME, E.RELATIVE_NAME)

# ---------------------------------------------------------------- identifiers


@pytest.mark.parametrize("text, etype, value", [
    ("Aadhaar 4821 7365 9012 ok", E.AADHAAR, "4821 7365 9012"),
    ("id 482173659012.", E.AADHAAR, "482173659012"),
    ("आधार ४८२१-७३६५-९०१२", E.AADHAAR, "4821-7365-9012"),
    ("card XXXX XXXX 4321", E.AADHAAR_MASKED, "XXXX XXXX 4321"),
    ("ABHA 91-1111-2222-3333", E.ABHA_NUMBER, "91-1111-2222-3333"),
    ("ask asha.v@abdm today", E.ABHA_ADDRESS, "asha.v@abdm"),
    ("mail a.b+c@example.co.in please", E.EMAIL, "a.b+c@example.co.in"),
    ("PAN ABCPE1234F filed", E.PAN, "ABCPE1234F"),
    ("call +91 98123 45670", E.PHONE, "+91 98123 45670"),
    ("call +91-9812345670", E.PHONE, "+91-9812345670"),
    ("call 098123-45670", E.PHONE, "098123-45670"),
    ("call 98123.45670", E.PHONE, "98123.45670"),
    ("no.9812345670", E.PHONE, "9812345670"),
    ("मोबाइल: ९८१२३४५६७०", E.PHONE, "9812345670"),
    ("office 0522-2345678", E.PHONE, "0522-2345678"),
    ("alt 98XXXXXX70", E.PHONE_MASKED, "98XXXXXX70"),
    ("claim PMJAY-UP-1234500000", E.INSURANCE_ID, "PMJAY-UP-1234500000"),
    ("Policy No: ZX/2291/88123", E.INSURANCE_ID, "ZX/2291/88123"),
    ("UHID: KMC/2026/00417", E.MRN, "KMC/2026/00417"),
    ("IPD No: ADM-22918", E.MRN, "ADM-22918"),
    ("पंजीकरण सं.: KMC-778120", E.MRN, "KMC-778120"),
    ("Passport No: M8812345", E.OTHER_ID, "M8812345"),
])
def test_identifiers(detector, text, etype, value):
    assert value in values(detector, text, etype)


@pytest.mark.parametrize("text", [
    "Tab Dolo 650 BD x 5 days", "1-0-1 after food", "Platelet 150000-410000 /uL",
    "Total WBC 9339 /uL", "LOINC 718-7, SNOMED 373942005", "BP 123/76 mmHg",
    "urn:uuid:b6ae9c60-4286-4730-8390-bd257f0acd4c", "Amount 118500", "Bed 29, Ward 4",
    "DM2 x 12 yrs, H/O snake bite 8 yrs back", "Reference range 70-140 mg/dL",
    "Paracetamol 650 mg 1-1-1 x 15 days", "Rifampicin + Isoniazid 450 mg + 300 mg",
])
def test_medical_values_are_never_personal(detector, text):
    assert found(detector, text) == []


# ---------------------------------------------------------------------- names


@pytest.mark.parametrize("text, name, role", [
    ("Patient Name: Qelvin Marbaniang    Age: 4", "Qelvin Marbaniang", Role.PATIENT),
    ("Name - Mrs. Wanrisha Kharbuli", "Wanrisha Kharbuli", Role.PATIENT),
    ("मरीज़ का नाम: ज़ोरावर फूकन", "ज़ोरावर फूकन", Role.PATIENT),
    ("रोगी का नाम: श्रीमती लवंगलता", "लवंगलता", Role.PATIENT),
    ("S/o Brihaspati Nongrum, adult", "Brihaspati Nongrum", Role.RELATIVE),
    ("w/o  Kerhulhoutuo", "Kerhulhoutuo", Role.RELATIVE),
    ("पति का नाम: ओजस्वित", "ओजस्वित", Role.RELATIVE),
    ("Attendant: Vizokhole, Mob.", "Vizokhole", Role.RELATIVE),
    ("Informant: Lhingneihkim Vaiphei.", "Lhingneihkim Vaiphei", Role.RELATIVE),
    ("called by Ibomcha Laishram 98123 45670", "Ibomcha Laishram", Role.RELATIVE),
    ("Witnessed by Temsula Ozukum, phone", "Temsula Ozukum", Role.RELATIVE),
    ("Akangla Imchen (attendant, bed 2)", "Akangla Imchen", Role.RELATIVE),
    ("Report of Nokinketla  48 Y / F", "Nokinketla", Role.PATIENT),
    ("Vizokhole ji ka BP 120/80", "Vizokhole", Role.PATIENT),
    ("Zuchamo sahab theek hain", "Zuchamo", Role.PATIENT),
    ("विरोचन साहब को आराम है", "विरोचन", Role.PATIENT),
    ("[30/05/26, 9:32 pm] Pfutsero Mama: hello", "Pfutsero Mama", Role.RELATIVE),
    ("14:00 - Sentilemla Jamir - K/C/O asthma", "Sentilemla Jamir", Role.PATIENT),
    ("Imkong, W/o Bendang, discharged", "Imkong", Role.PATIENT),
    ("C/O Wapangsenla Longkumer, H.No. 4", "Wapangsenla Longkumer", Role.RELATIVE),
    ("Pt. Wanrisha admitted with fever.", "Wanrisha", Role.PATIENT),
    ("Patient Temsula Ozukum was seen today.", "Temsula Ozukum", Role.PATIENT),
    ("मरीज़ वानरिशा (उम्र ३० वर्ष)", "वानरिशा", Role.PATIENT),
    ("Mareez ka naam Zuchamo hai", "Zuchamo", Role.PATIENT),
    ("Name: Kharbuli, Wanrisha | Sex: F", "Kharbuli, Wanrisha", Role.PATIENT),
])
def test_context_rules_need_no_name_list(bare_lexicon, text, name, role):
    from wards.textview import TextView

    mentions = Detector(bare_lexicon).detect_segment(Segment(text))
    expected = TextView.of(name).text
    match = [m for m in mentions if m.etype in NAMES and m.value == expected]
    assert match, [(m.etype.value, m.value) for m in mentions]
    assert match[0].role is role


@pytest.mark.parametrize("text, name", [
    ("Naveen Bajpai came in today", "Naveen Bajpai"),
    ("KUSUM DEVI follow up", "KUSUM DEVI"),
    ("Mohd. Faizan reviewed his diet", "Mohd. Faizan"),
    ("R. K. Sharma admitted", "R. K. Sharma"),
    ("कल गिरीश तिवारी आए थे", "गिरीश तिवारी"),
    ("ASHA BAGHEL", "ASHA BAGHEL"),
])
def test_gazetteer_names(detector, text, name):
    from wards.textview import TextView

    assert TextView.of(name).text in values(detector, text, *NAMES)


@pytest.mark.parametrize("text", [
    "Dr. Girish Negi reviewed the patient.",
    "डॉ. गिरीश वर्मा ने देखा",
    "Consultant: Dr. Sunita Rao (MD Medicine)",
    "Dr. A. K. Mehra (MD Pulmonary Medicine)",
    "Dr sahab ne bola aaram karo",
    "K/C/O Addison's disease", "History of Parkinson's disease", "Down syndrome",
    "Bell's palsy resolved", "Referred by ASHA worker of the village.",
    "आशा कार्यकर्ता ने भेजा", "ASHA workers should follow up on day 3.",
    "Prem Jyoti Memorial Hospital", "Shri Ram Hospital", "C/O fever for 6 days",
    "C/O headache and giddiness x 3 days", "K/C/O hypothyroidism",
    "H/O TB 2 yrs back (completed ATT)", "Any patient presenting with C/O fever",
    "Ward/Bed: Ward 8 (Medicine) / Bed 23", "Patient ne raat theek se neend li.",
    "Course in hospital: Patient admitted with above complaints.",
    "Medicine: Ramipril 5 mg", "Review after 2 wks.", "मरीज़ को 8 दिन से बुखार था।",
    "Hospital Name: Sunrise Care", "Drug Name: Telmisartan", "Patient Education given.",
    "Pt. complains of pain.", "मरीज को पेट दर्द और उल्टी की शिकायत है।",
    "Aaj subah se bukhar hai, kal dobara aana.",
])
def test_veto_keeps_doctors_diseases_and_facilities(detector, text):
    assert values(detector, text, *NAMES) == []


def test_relative_is_removed_while_the_doctor_stays(detector):
    text = "Attendant: Girish Tripathi. Dr. Girish Negi reviewed."
    assert values(detector, text, *NAMES) == ["Girish Tripathi"]


# ------------------------------------------------------------------ addresses


def test_english_address(detector):
    text = ("Address: Makan No. 12, Ward 6, Mohalla Nai Basti, near Hanuman Mandir, "
            "Vill. Amarpur Khurd, Tehsil Nawabganj, Distt. Barabanki - 123456")
    got = dict((value, etype) for etype, value in found(detector, text))
    assert got["Makan No. 12"] is E.HOUSE
    assert got["Ward 6"] is E.LOCALITY and got["Mohalla Nai Basti"] is E.LOCALITY
    assert got["near Hanuman Mandir"] is E.LANDMARK
    assert got["Amarpur Khurd"] is E.VILLAGE
    assert got["Nawabganj"] is E.TEHSIL
    assert got["123456"] is E.PIN
    assert "Barabanki" not in got


def test_hindi_address(detector):
    text = "पता: मकान नं. 41, हनुमान मंदिर के पास, ग्राम शिवपुर, तहसील नानपारा, जिला बहराइच - 654321"
    kinds = {etype for etype, _ in found(detector, text)}
    assert kinds >= {E.HOUSE, E.LANDMARK, E.VILLAGE, E.TEHSIL, E.PIN}
    assert E.PATIENT_NAME not in kinds


def test_pin_after_a_district_in_prose(detector):
    assert values(detector, "lives in Distt. Mainpuri - 205265 now", E.PIN) == ["205265"]


def test_resident_of_phrase(detector):
    got = found(detector, "Smt. X R/o Village Nagla Sundar, PS Chakmau, Distt. Mainpuri now")
    assert (E.VILLAGE, "Nagla Sundar") in got and (E.LOCALITY, "Chakmau") in got


def test_village_capture_stops_at_ordinary_words(detector):
    assert values(detector, "ग्राम चकमऊ की निवासी हैं", E.VILLAGE) == ["चकमऊ"]


def test_unknown_address_part_is_removed_to_be_safe(detector):
    got = values(detector, "Chak Imaliya, Barabanki", E.VILLAGE, field=F.ADDRESS)
    assert got == ["Chak Imaliya"]


def test_district_values_are_kept(detector):
    assert found(detector, "Rae Bareli", F.VILLAGE) == []
    assert found(detector, "सीतापुर", F.DISTRICT) == []


# ---------------------------------------------------------------- ages, dates


@pytest.mark.parametrize("text, age", [
    ("Age: 52", "52"), ("Age/Sex: 52 Yrs/F", "52"), ("48 Y / F", "48"), ("35/F", "35"),
    ("आयु: २० वर्ष", "20"), ("उम्र 40", "40"), ("97 yrs, male", "97"),
    ("a 45-year-old woman", "45"), ("aged 61", "61"), ("umar 45 saal", "45"),
    ("45 saal ki mahila", "45"), ("६० साल की महिला", "60"),
])
def test_ages_with_context(detector, text, age):
    assert values(detector, text, E.AGE) == [age]


@pytest.mark.parametrize("text", [
    "DM2 x 9 yrs", "TB 2 yrs back", "for 3 years", "BP 52/90", "50-54 Yrs/F", "90+ yrs, male",
])
def test_durations_and_bands_are_not_ages(detector, text):
    assert values(detector, text, E.AGE) == []


@pytest.mark.parametrize("value, expected", [
    ("45-49", []), ("90+", []), ("47 वर्ष", ["47"]), ("35 yrs 2 months", ["35"]),
    ("8 months", ["8 months"]), ("45.5", ["45"]), ("-2 yrs", ["-2 yrs"]),
])
def test_typed_age_values(detector, value, expected):
    assert values(detector, value, E.AGE, field=F.AGE) == expected


def test_birth_dates_are_told_apart(detector):
    kinds = [t for t, _ in found(detector, "DOB: 12/04/1977\nAdmitted: 01/02/2026")]
    assert kinds == [E.DOB, E.DATE]


# -------------------------------------------------------------- typed values


def test_structured_name_with_relation(detector):
    mentions = Detector(detector.lexicon).detect_segment(
        Segment("S/O GOPAL TOMAR", field=F.RELATIVE_NAME))
    assert [(m.value, m.role) for m in mentions] == [("GOPAL TOMAR", Role.RELATIVE)]


@pytest.mark.parametrize("value", ["", "NA", "-", "None", "unknown"])
def test_empty_markers_are_not_redacted(detector, value):
    assert found(detector, value, F.PATIENT_NAME) == []


def test_shifted_column_is_still_detected(detector):
    assert values(detector, "9812345670", E.PHONE, field=F.PATIENT_NAME) == ["9812345670"]
    assert values(detector, "Asha Verma", *NAMES, field=F.PHONE) == ["Asha Verma"]


def test_doctor_field_keeps_name_but_not_phone(detector):
    got = found(detector, "Anil Mehra 9812345670", F.DOCTOR_NAME)
    assert got == [(E.PHONE, "9812345670")]


def test_unowned_name_key_ignores_things(detector):
    assert found(detector, "Routine", F.NAME) == []
    assert found(detector, "City Diagnostics", F.NAME) == []
    assert values(detector, "Wanrisha Kharbuli", *NAMES, field=F.NAME)


def test_encoded_payload_inside_text(detector):
    blob = base64.b64encode(b"Witnessed by Temsula Ozukum, mob 9812345670").decode()
    assert blob in values(detector, f"scan: {blob}", E.OTHER_ID)


@pytest.mark.parametrize("path, expected", [
    ("Discharge_Summary_Asha_Verma", ["Asha_Verma"]),
    ("D:\\exports\\opd\\Wanrisha_Kharbuli_088771", ["Wanrisha_Kharbuli"]),
    ("rx_sanjeevani_00024", []),
    ("ward_vitals_sheet", []),
    ("drug_formulary", []),
])
def test_names_in_paths(detector, path, expected):
    assert values(detector, path, *NAMES, field=F.PATH) == expected

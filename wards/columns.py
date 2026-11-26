"""Decide what a CSV column or JSON key holds.

Headers are matched by keyword (English, Hinglish and Hindi) rather than exact string, so
renamed columns such as ``Patient's Name`` or ``Contact#`` still classify. Values are
checked too: a column with an unfamiliar header is typed by what its cells look like.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from wards.lexicon import Lexicon
from wards.model import FieldType as F
from wards.textview import TextView

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[^0-9a-zऀ-ॣॱ-ॿ]+")


def normalize_label(label: str) -> str:
    """``"Pita/Pati ka Naam"`` -> ``" pita pati ka naam "`` (padded for whole-word tests)."""
    text = TextView.of(label).text
    text = _CAMEL.sub(" ", text)
    text = text.replace("'s", "").replace("’s", "").lower()
    return f" {_SEPARATORS.sub(' ', text).strip()} "


def _phrases(spec: str) -> tuple[str, ...]:
    return tuple(f" {TextView.of(p.strip()).text} " for p in spec.split("|"))


# First match wins, so the specific rules come before the generic ones.
_RULES: tuple[tuple[F, tuple[str, ...]], ...] = tuple((kind, _phrases(spec)) for kind, spec in (
    (F.CODE, "visit id|bill no|bill number|sample id|loinc|package code|icd code|resource type|"
             "full url|trip|schema|version|ticket id|accession no|barcode"),
    (F.ABHA_ADDRESS, "abha address|abha addr|phr address|health address"),
    (F.ABHA_NUMBER, "abha|abha no|abha number|abha id|health id|health account"),
    (F.AADHAAR, "aadhaar|aadhar|adhaar|adhar|aadhaar no|aadhar no|uidai|uid no|आधार"),
    (F.PAN, "pan|pan no|pan number|pan card|payer pan|पैन"),
    (F.EMAIL, "email|e mail|mail id|email id|ईमेल"),
    (F.PHONE, "phone|mobile|mob|mobile no|phone no|telephone|tel|callback|call back|whatsapp|"
              "contact no|contact number|contact num|cell|cell no|ph no|ph|fone|मोबाइल|फोन|"
              "फ़ोन|दूरभाष|संपर्क"),
    (F.MRN, "uhid|mrn|mr no|mr number|patient id|patientid|patient ref|patient no|"
            "patient number|patient code|hospital id|hospital no|reg no|regno|reg id|"
            "registration no|registration number|registration id|ipd|ipd no|ipd number|"
            "ip no|ip number|opd no|admission no|admission number|adm no|crn|cr no|"
            "medical record|record no|file no|case no|पंजीकरण|पंजीकरण सं|आईपीडी|आईपीडी सं"),
    (F.INSURANCE_ID, "pmjay|pmjay id|policy|policy no|policy number|insurance|insurance id|"
                     "insurance no|tpa id|claim no|claim id|card no|ayushman|beneficiary id|"
                     "member id|बीमा"),
    (F.DOB, "dob|date of birth|birth date|birthdate|birth dt|born on|janm|janm tithi|"
            "janam tithi|जन्म|जन्म तिथि|जन्मतिथि"),
    (F.DATE, "date|dt|dated|datetime|date time|collected|collected on|reported on|"
             "registered|registered on|generated at|exported at|authored on|timestamp|"
             "last updated|admitted on|discharged on|start|end|doa|dod|doj|tareekh|tarikh|"
             "तारीख|दिनांक|तिथि"),
    (F.AGE_SEX, "age sex|age gender|age and sex|sex age|umar ling|आयु लिंग"),
    (F.AGE, "age|age years|age yrs|age in years|years|yrs|umar|umra|umr|aayu|उम्र|आयु"),
    (F.SEX, "sex|gender|ling|लिंग"),
    (F.DOCTOR_NAME, "doctor|doctor name|dr|dr name|ref doctor|referring doctor|consultant|"
                    "physician|surgeon|referred by|reviewed by|prescribed by|treating doctor|"
                    "author|practitioner|चिकित्सक|डॉक्टर"),
    (F.CODE, "relation|relationship|relation type|rel type"),
    (F.RELATIVE_NAME, "relative|relative name|guardian|guardian name|father|father name|"
                      "fathers name|mother|mother name|husband|husband name|spouse|"
                      "spouse name|next of kin|kin|kin name|attendant|attendant name|"
                      "nominee|care of|pita|pati|pita pati|pati pita|abhibhavak|w o|s o|"
                      "d o|so wo do|पिता|पति|माता|अभिभावक|पिता का नाम|पति का नाम|"
                      "पिता पति|पिता पति का नाम"),
    (F.PIN, "pin|pincode|pin code|zip|zipcode|zip code|postal|postal code|पिन|पिन कोड"),
    (F.VILLAGE, "village|gram|gaon|gav|city|town|village mohalla|village town|"
                "ग्राम|गांव|गाँव|गाव"),
    (F.TEHSIL, "tehsil|tahsil|taluk|taluka|block|teh|तहसील|ब्लॉक"),
    (F.DISTRICT, "district|distt|dist|jila|zila|zilla|जिला|ज़िला|जनपद"),
    (F.STATE, "state|country|राज्य|देश"),
    (F.HOUSE, "house|house no|house number|makan|makan no|flat no|door no|मकान"),
    (F.LOCALITY, "mohalla|muhalla|locality|colony|ward mohalla|मोहल्ला|मुहल्ला"),
    (F.LANDMARK, "landmark|land mark"),
    (F.ADDRESS, "address|addr|add|pata|street|address line|address note|residence|"
                "residential address|पता|निवास"),
    (F.PERSON_NAME, "payer name|paid by|payee"),
    (F.ORG, "hospital|hospital name|facility|facility name|lab|lab name|laboratory|"
            "organisation|organization|department|dept|ward|ward name|unit|payer|centre|"
            "center|branch|system|अस्पताल|विभाग"),
    (F.CLINICAL, "drug|drug name|medicine|strength|frequency|duration|dose|dosage|test|"
                 "test name|result|value|unit|units|range|ref range|reference range|"
                 "diagnosis|display|procedure|procedure name|vitals|bp|pulse|temp|temp f|"
                 "spo2|weight|weight kg|height|beds|occupied|dengue cases|amount|"
                 "amount inr|category|schedule|form|status|panel|panels|specialty|code|"
                 "type|week|निदान|दवा"),
    (F.FIRST_NAME, "first name|firstname|fname|f name|given|given name|forename"),
    (F.LAST_NAME, "last name|lastname|lname|l name|surname|family|family name|upnaam|उपनाम"),
    (F.PATIENT_NAME, "patient name|pt name|name of patient|patient full name|patient|"
                     "name|full name|fullname|naam|mareez|mareez ka naam|rogi ka naam|"
                     "नाम|मरीज़ का नाम|मरीज का नाम|रोगी का नाम"),
    (F.TEXT, "notes|note|remarks|remark|history|complaint|complaints|comment|comments|"
             "description|narrative|div|message|summary|details|टिप्पणी"),
))

# "Hospital Name", "Drug Name", ...: the word "name" belongs to a thing, not a person.
_THING_NAME = _phrases("hospital|facility|lab|laboratory|drug|medicine|test|panel|"
                       "procedure|department|ward|file|brand|generic|product|package|"
                       "organisation|organization|scheme|bank|branch|village|district")


def classify_label(label: str) -> F | None:
    """Field type suggested by a header or key, or ``None`` when it says nothing."""
    text = normalize_label(label)
    if text.strip() in ("", "id"):
        return F.CODE if text.strip() else None
    if text == " contact ":
        return F.PHONE
    if text.endswith((" name ", " naam ")) and any(p in text for p in _THING_NAME):
        return F.ORG
    for kind, phrases in _RULES:
        for phrase in phrases:
            if phrase in text:
                return kind
    return None


_LABEL_WORDS = frozenset(word for _, phrases in _RULES for p in phrases for word in p.split())


def is_schema_label(label: str, lexicon: Lexicon) -> bool:
    """A header or key that merely names a field (``Umar``, ``Pita/Pati ka Naam``).

    A label carrying digits or an unfamiliar person-like word may hold data, so it is
    still inspected.
    """
    if classify_label(label) is None or any(ch.isdigit() for ch in label):
        return False
    words = normalize_label(label).split()
    return all(w in _LABEL_WORDS or not lexicon.is_known_name(w) for w in words)


# --------------------------------------------------------------------- value shapes

_SHAPES: tuple[tuple[F, re.Pattern[str]], ...] = (
    (F.EMAIL, re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+$")),
    (F.PAN, re.compile(r"[A-Z]{5}\d{4}[A-Z]$")),
    (F.ABHA_NUMBER, re.compile(r"\d{2}-\d{4}-\d{4}-\d{4}$")),
    (F.AADHAAR, re.compile(r"\d{4}([ -]?)\d{4}\1\d{4}$|[Xx*]{4}[ -]?[Xx*]{4}[ -]?\d{4}$")),
    (F.PHONE, re.compile(r"(?:\+?91[ -]?|0)?[6-9]\d{2,4}[ -]?\d{2,3}[ -]?\d{3,5}$")),
    (F.DATE, re.compile(r"\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d\d$|(?:19|20)\d\d-\d\d-\d\d")),
)
_ADDRESS_MARKER = re.compile(
    r"(?i)\b(h\.?\s?no|house|makan|mohalla|tehsil|distt?\.|vill\.|gram|near|ward)\b"
    r"|मकान|मोहल्ला|तहसील|जिला|ग्राम")
_NAME_TOKEN = re.compile(r"[A-Za-z]+\.?|[ऀ-ॣॱ-ॿ]+")


def _looks_like_name(value: str, lexicon: Lexicon) -> bool:
    if any(ch.isdigit() for ch in value):
        return False
    tokens = _NAME_TOKEN.findall(value)
    if not 1 <= len(tokens) <= 5 or any(lexicon.is_stopword(t) for t in tokens):
        return False
    return any(lexicon.is_known_name(t) for t in tokens)


def infer_from_values(values: Sequence[str], lexicon: Lexicon) -> F | None:
    cells = [TextView.of(v).text.strip() for v in values if v and v.strip()][:200]
    if len(cells) < 2:
        return None
    threshold = 0.6 * len(cells)
    for kind, pattern in _SHAPES:
        if sum(bool(pattern.match(c)) for c in cells) >= threshold:
            return kind
    if sum(len(_ADDRESS_MARKER.findall(c)) >= 2 for c in cells) >= threshold:
        return F.ADDRESS
    if sum(_looks_like_name(c, lexicon) for c in cells) >= threshold:
        return F.PERSON_NAME
    return None


_PATTERNED = frozenset({F.EMAIL, F.PAN, F.ABHA_NUMBER, F.AADHAAR, F.PHONE})


def type_columns(header: Sequence[str], rows: Sequence[Sequence[str]],
                 lexicon: Lexicon) -> list[F]:
    types: list[F] = []
    for index, label in enumerate(header):
        suggested = classify_label(label)
        values = [row[index] for row in rows if index < len(row)]
        inferred = infer_from_values(values, lexicon)
        if suggested is None or suggested is F.CODE and inferred in _PATTERNED:
            kind = inferred or suggested or F.TEXT
        elif suggested in (F.PATIENT_NAME, F.RELATIVE_NAME) and inferred in _PATTERNED:
            kind = inferred          # shifted columns: trust the values
        else:
            kind = suggested
        types.append(kind)
    # An unnamed person column is the patient only if no column already claims that role.
    has_patient = any(t in (F.PATIENT_NAME, F.FIRST_NAME, F.LAST_NAME) for t in types)
    if not has_patient:
        for index, kind in enumerate(types):
            if kind is F.PERSON_NAME:
                types[index] = F.PATIENT_NAME
                break
    return types


# ------------------------------------------------------------------------ JSON keys

_PERSON_CONTAINERS: tuple[tuple[F, tuple[str, ...]], ...] = tuple(
    (kind, _phrases(spec)) for kind, spec in (
        (F.DOCTOR_NAME, "doctor|consultant|physician|practitioner|author|surgeon|"
                        "referred by|reviewed by"),
        (F.ORG, "facility|hospital|organization|organisation|lab|panel|panels|department|"
                "ward|wards|payer"),
        (F.RELATIVE_NAME, "guardian|relative|relatives|father|mother|husband|spouse|kin|"
                          "attendant|nominee|emergency contact"),
        (F.PERSON_NAME, "raised by|witness|caller|reported by|informant|signed by|"
                        "contact person|contact|requested by|created by|collected by|user"),
        (F.PATIENT_NAME, "patient|patients|pt|pat|beneficiary|member|subject|client|insured"),
    ))
_GENERIC_NAME_KEYS = frozenset({"name", "text", "full", "full name", "display name", "local"})
_ALWAYS_A_NAME = frozenset({"name", "full name", "display name"})
_NAME_PART_KEYS = {"first": F.FIRST_NAME, "given": F.FIRST_NAME, "first name": F.FIRST_NAME,
                   "last": F.LAST_NAME, "family": F.LAST_NAME, "last name": F.LAST_NAME}


def _container_role(ancestors: Sequence[str]) -> F | None:
    for key in reversed(ancestors):
        text = normalize_label(key)
        for kind, phrases in _PERSON_CONTAINERS:
            if any(p in text for p in phrases):
                return kind
    return None


def classify_json_key(path: Sequence[str]) -> F:
    """Field type for a JSON leaf, given the object keys leading to it (no indices)."""
    if not path:
        return F.TEXT
    key = normalize_label(path[-1]).strip()
    ancestors = path[:-1]
    parent = normalize_label(ancestors[-1]).strip() if ancestors else ""

    if key in ("source path", "path", "file path", "filepath", "filename", "file name"):
        return F.PATH
    if key == "line" and "address" in parent:
        return F.ADDRESS

    nested_in_name = parent in ("name", "names", "full name")
    if key in _NAME_PART_KEYS or key == "middle" or (key in _GENERIC_NAME_KEYS):
        people = ancestors[:-1] if nested_in_name else ancestors
        role = _container_role(people)
        if key in _NAME_PART_KEYS or key == "middle":
            if role in (F.RELATIVE_NAME, F.DOCTOR_NAME, F.PERSON_NAME):
                return role
            return _NAME_PART_KEYS.get(key, F.FIRST_NAME) if nested_in_name else F.TEXT
        if role is not None:
            return role
        if nested_in_name:
            return F.PATIENT_NAME
        return F.NAME if key in _ALWAYS_A_NAME else F.TEXT

    kind = classify_label(path[-1])
    if kind is F.PATIENT_NAME:
        role = _container_role(ancestors)
        if role in (F.DOCTOR_NAME, F.RELATIVE_NAME, F.PERSON_NAME):
            return role
    if kind is F.CODE and key in ("relation", "relationship"):
        return F.CODE
    return kind or F.TEXT


# ----------------------------------------------------------------------------- FHIR

_FHIR_DATE_KEYS = frozenset({
    "date", "lastUpdated", "timestamp", "recordedDate", "effectiveDateTime", "authoredOn",
    "performedDateTime", "start", "end", "issued", "occurrenceDateTime", "onsetDateTime",
    "abatementDateTime", "created", "recorded", "deceasedDateTime", "assertedDate",
})
_FHIR_CODE_KEYS = frozenset({
    "resourceType", "id", "fullUrl", "reference", "system", "url", "profile", "status",
    "intent", "code", "use", "contentType", "language", "versionId", "div_status",
})


def classify_fhir(path: Sequence[str], resource: str, siblings: dict) -> F:
    """Field type for a FHIR leaf, using the resource type and sibling elements."""
    key = path[-1] if path else ""
    trail = set(path[:-1])

    if key == "div":
        return F.TEXT
    if key in ("source", "title") and (key == "source" or "attachment" in trail):
        return F.TEXT
    if key == "display":
        if "coding" in trail:
            return F.CLINICAL
        if "subject" in trail or "patient" in trail:
            return F.PATIENT_NAME
        if trail & {"author", "performer", "requester", "recorder", "asserter", "actor",
                    "individual", "practitioner", "attester", "enterer"}:
            return F.DOCTOR_NAME
        if trail & {"serviceProvider", "custodian", "organization", "managingOrganization"}:
            return F.ORG
        if "location" in trail:
            return F.CLINICAL
        return F.TEXT
    if key in _FHIR_DATE_KEYS:
        return F.DATE

    if resource in ("Organization", "Location", "HealthcareService"):
        return F.CODE if key in _FHIR_CODE_KEYS else F.ORG

    person = None
    if resource in ("Patient", "Person"):
        person = F.PATIENT_NAME
    elif resource in ("Practitioner", "PractitionerRole"):
        person = F.DOCTOR_NAME
    elif resource == "RelatedPerson":
        person = F.RELATIVE_NAME

    if "name" in path and person and key in ("text", "given", "family", "prefix", "suffix",
                                             "name"):
        if key in ("prefix", "suffix"):
            return F.CODE
        if "contact" in trail:
            return F.RELATIVE_NAME
        if person is F.PATIENT_NAME and key == "given":
            return F.FIRST_NAME
        if person is F.PATIENT_NAME and key == "family":
            return F.LAST_NAME
        return person

    if key == "value" and "telecom" in trail:
        system = str(siblings.get("system", "")).lower()
        return F.EMAIL if system == "email" else F.PHONE
    if key == "value" and "identifier" in trail:
        return _fhir_identifier(resource, siblings)
    if key == "valueString" and "extension" in trail:
        url = str(siblings.get("url", "")).lower()
        if re.search(r"mother|father|spouse|husband|wife|guardian|kin|relative", url):
            return F.RELATIVE_NAME
        return F.TEXT
    if key == "birthDate":
        return F.DOB if resource in ("Patient", "Person", "RelatedPerson") else F.DATE
    if key == "gender":
        return F.SEX
    if "address" in trail:
        return {"line": F.ADDRESS, "text": F.ADDRESS, "city": F.VILLAGE,
                "district": F.DISTRICT, "postalCode": F.PIN, "state": F.STATE,
                "country": F.STATE}.get(key, F.CODE)
    if key in _FHIR_CODE_KEYS:
        return F.CODE
    if key in ("unit", "value") or trail & {"code", "medicationCodeableConcept",
                                            "dosageInstruction", "valueQuantity", "type",
                                            "class", "category", "clinicalStatus"}:
        return F.CLINICAL
    if key == "title":
        return F.CODE
    return F.TEXT


def _fhir_identifier(resource: str, siblings: dict) -> F:
    system = str(siblings.get("system", "")).lower()
    if resource in ("Bundle", "Organization", "Practitioner", "Location"):
        return F.CODE
    if "abdm" in system or "healthid" in system or "abha" in system:
        return F.ABHA_NUMBER
    if "aadhaar" in system or "uidai" in system or "2.16.356" in system:
        return F.AADHAAR
    if re.search(r"pmjay|insurance|policy|coverage", system):
        return F.INSURANCE_ID
    return F.MRN

"""Whose number is it? Label words and value shapes shared by the identifier rules.

A hospital number, a claim number and a bed number can look exactly alike. What tells them
apart is the wording around them, and a few shapes that are never anybody's identifier.
"""

from __future__ import annotations

import re
import unicodedata


def _words(spec: str) -> frozenset[str]:
    return frozenset(unicodedata.normalize("NFKC", spec).split())


# Words saying that a number belongs to an event, a thing, a place, a member of staff or
# another kind of identifier: it is not the patient's hospital number.
_NOT_PERSONAL = _words(
    "bill billing invoice receipt voucher claim preauth auth authorisation authorization "
    "approval sample specimen accession barcode lab report result test panel visit encounter "
    "episode appointment token queue order requisition request indent batch lot room bed ward "
    "unit house h flat door plot shop sector block gali street road package pkg protocol trial "
    "study code model serial sl sr s device equipment asset kit implant machine instrument item "
    "product stock drug medicine rx prescription certificate form document doc version schema "
    "page icd loinc snomed hsn sac gst gstin txn transaction payment utr cheque challan account "
    "policy insurance tpa member membership licence license council mci nmc nabh nabl doctor dr "
    "staff employee emp consultant phone mobile mob ph tel contact helpline fax extn ext aadhaar "
    "aadhar uid pan abha vehicle tender circular notification section rule clause scheme pin zip "
    "toll free emergency ambulance isd std survey census order go gazette ctri nct "
    "amount amt rs inr estimate total count qty quantity dose value platelet platelets plt wbc "
    "rbc tlc dlc cell cells haemoglobin hemoglobin hb sugar level reading score titre load "
    "बिल रसीद दावा नमूना जांच जाँच रिपोर्ट विज़िट टोकन कमरा बिस्तर वार्ड मकान गली क्रम मोबाइल "
    "फोन फ़ोन मो आधार खाता पॉलिसी बीमा राशन बैच लॉट कोड डॉक्टर कर्मचारी राशि वाहन लाइसेंस")
# Words that make it the patient's own number at a hospital.
_PERSONAL = _words(
    "ip ipd inpatient indoor op opd outpatient uhid mr mrn mrd crn cr reg regn registration "
    "patient pt hosp hospital adm admission file case record chart पंजीकरण आईपीडी ओपीडी "
    "रजिस्ट्रेशन रोगी मरीज मरीज़ भर्ती अंतःरोगी")
# Unambiguous ones: nothing further to the left changes whose number it is.
_ALWAYS_PERSONAL = _words("ip ipd inpatient indoor uhid mrn mrd crn patient pt आईपीडी रोगी "
                          "मरीज मरीज़")
_LABEL_WORD = re.compile(r"[A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+")
_LABEL_BREAK = re.compile(r"[^A-Za-z\u0900-\u097f .'’/\-_&\t]")
_BEFORE_VALUE = " \t:#=-–—.(["


def label_words(label: str) -> list[str]:
    """Lower-case words of a label, with initials joined: "I.P. No" -> ["ip", "no"]."""
    words: list[str] = []
    initials = ""
    for word in _LABEL_WORD.findall(unicodedata.normalize("NFKC", label).lower()):
        if len(word) == 1 and word.isascii():
            initials += word
            continue
        if initials:
            words.append(initials)
            initials = ""
        words.append(word)
    return words + [initials] if initials else words


def words_before(text: str, start: int, count: int = 3) -> list[str]:
    """The label words just left of ``start``, stopping at punctuation or digits."""
    line = text[max(0, start - 40):start].rsplit("\n", 1)[-1].rstrip(_BEFORE_VALUE)
    return label_words(_LABEL_BREAK.split(line)[-1])[-count:]


def personal_label(words: list[str]) -> bool:
    """Do the words of a label say that its number is a hospital number?"""
    return any(word in _PERSONAL for word in words)


def foreign_number(words: list[str]) -> bool:
    """Do the words of a label say that its number is not the patient's own?"""
    for word in reversed(words):
        singular = word[:-1] if len(word) > 3 and word.endswith("s") else word
        if word in _NOT_PERSONAL or singular in _NOT_PERSONAL:
            return True
        if word in _ALWAYS_PERSONAL:
            return False
    return False


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
# Values that are something else whatever label stands before them.
_OTHER_THINGS = re.compile(
    r"(?:\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}"                                     # dates
    r"|(?:19|20)\d\d[\-/.]\d{1,2}[\-/.]\d{1,2}(?:T[\d.]*)?"
    rf"|(?:\d{{1,2}}[\-/.]?)?{_MONTH}[\-/.]?\d{{2,4}}"
    r"|(?:19|20)\d\d(?:[\-/](?:\d\d|(?:19|20)\d\d))?"                              # years
    r"|[A-TV-Z]\d[0-9A-Z](?:\.[0-9A-Z]{1,4})?"                                      # ICD-10
    r"(?:-[A-TV-Z]\d[0-9A-Z](?:\.[0-9A-Z]{1,4})?)?"
    r"|\d{1,5}-\d"                                                                # LOINC
    r"|\d+\.\d+|\d{1,3}-\d{1,3}"                                                    # results
    r"|(?:[A-Za-z]{1,12}-?)?\d+(?:\.\d+)?(?:mg|mcg|ug|µg|g|gm|kg|ml|l|iu|u|units?|mmhg"
    r"|bpm|cm|mm|d|days?|y|yrs?|years?|m|months?|hrs?|h|am|pm|k|x|st|nd|rd|th)(?:/[a-z]+)?"
    r"|(?:91)?[6-9]\d{9}"                                                           # mobiles
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f\-]+)", re.IGNORECASE)


def reads_as_other(value: str) -> bool:
    """A date, year, clinical code, measurement, mobile number or UUID."""
    return _OTHER_THINGS.fullmatch(value) is not None

"""Identifier detectors: government and health IDs, phones, e-mail, hospital numbers."""

from __future__ import annotations

import re
import unicodedata

from wards.model import EntityType as E
from wards.model import Mention
from wards.numbers import foreign_number, label_words, personal_label, reads_as_other, words_before


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(unicodedata.normalize("NFKC", pattern), flags)


# A phone core: ten digits starting 6-9, in the groupings people actually type.
_MOBILE = (r"(?:[6-9]\d{9}|[6-9]\d{4}[ \t.\-]\d{5}|[6-9]\d{2}[ \t.\-]\d{3}[ \t.\-]\d{4}"
           r"|[6-9]\d{3}[ \t.\-]\d{3}[ \t.\-]\d{3}|[6-9]\d{3}[ \t\-]\d{6})")

_PATTERNS: tuple[tuple[E, int, re.Pattern[str]], ...] = (
    (E.ABHA_ADDRESS, 96, _compile(r"(?<![\w.+\-])[\w.\-]+@(?:abdm|sbx|ndhm)(?![\w])", re.I)),
    (E.EMAIL, 95, _compile(r"(?<![\w.+\-])[\w.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")),
    (E.OTHER_ID, 94, _compile(
        r"(?<![\w.\-])[\w.\-]{2,}@(?:ok[a-z]+|ybl|paytm|upi|ibl|axl|apl|sbi|hdfcbank|icici)"
        r"(?![\w.])", re.I)),
    (E.INSURANCE_ID, 93, _compile(
        r"(?<![A-Za-z0-9])(?:AB-?)?PM-?JAY[\-/ ]?(?:[A-Z]{2,3}[\-/ ]?)?\d{6,16}(?!\d)", re.I)),
    (E.INSURANCE_ID, 93, _compile(
        r"(?<![A-Za-z0-9])(?:POL|POLICY|INS|TPA|CLM)[\-/]\d{1,6}(?:[\-/]\d{2,14})+(?!\d)", re.I)),
    (E.PAN, 90, _compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")),
    (E.ABHA_NUMBER, 89, _compile(
        r"(?<![\d])(?:\d{2}[ \-]\d{4}[ \-]\d{4}[ \-]\d{4}|\d{14})(?!\d)")),
    (E.AADHAAR_MASKED, 88, _compile(
        r"(?<![A-Za-z0-9])(?:[Xx*•]{4}[ \-]?){2}\d{4}(?!\d)")),
    (E.AADHAAR, 86, _compile(r"(?<!\d)\d{4}(?P<sep>[ \-.]?)\d{4}(?P=sep)\d{4}(?!\d)")),
    (E.PHONE_MASKED, 85, _compile(
        r"(?<![A-Za-z0-9])(?:\+?91[ \-]?)?[6-9]\d{1,4}[ \-]?[Xx*•]{3,7}[ \-]?\d{2,4}(?!\d)")),
    (E.PHONE_MASKED, 85, _compile(r"(?<![A-Za-z0-9])[Xx*•]{5,7}[ \-]?\d{3,5}(?!\d)")),
    (E.PHONE, 82, _compile(
        r"(?<!\d)(?:(?:\+|00)91[ \t.\-]{0,3}|\(\+?91\)[ \t\-]?|91[ \t\-]|0[ \-]?)?"
        + _MOBILE + r"(?!\d)")),
    # landline with an STD code
    (E.PHONE, 78, _compile(r"(?<![\dA-Za-z/\-])0\d{2,4}[ \-]\d{6,8}(?![\d/\-])")),
    (E.OTHER_ID, 60, _compile(r"(?<![A-Za-z0-9])[A-Z]{3}\d{7}(?![A-Za-z0-9])")),
)

_ID_VALUE = r"(?P<id>[A-Za-z0-9][A-Za-z0-9/\-_.]{2,34}[A-Za-z0-9])"

# "<label>: <value>" for hospital numbers, in English and Hindi.
_MRN_LABEL = _compile(
    r"(?<![A-Za-z])(?:UHID|U\.H\.I\.D\.?|MRN|MR[ \t]*(?:No|Number)|IPD(?:[ \t]*(?:No|Number|ID))?"
    r"|IP[ \t]*(?:No|Number)|OPD[ \t]*(?:No|Number)|CR[ \t]*(?:No|Number)|CRN"
    r"|Reg(?:n|d|istration)?\.?[ \t]*(?:No|Number|ID)|Patient[ \t]*(?:ID|No|Number|Code)"
    r"|Hosp(?:ital)?\.?[ \t]*(?:ID|No|Number|UHID)|Adm(?:ission)?\.?[ \t]*(?:No|Number|ID)"
    r"|(?:Medical[ \t]*)?Record[ \t]*(?:No|Number)|File[ \t]*No|Case[ \t]*No"
    r"|पंजीकरण[ \t]*(?:सं|संख्या|नं|क्रमांक)|आईपीडी[ \t]*(?:सं|संख्या|नं)"
    r"|ओपीडी[ \t]*(?:सं|संख्या|नं)|रजिस्ट्रेशन[ \t]*(?:सं|नं))"
    r"\.?[ \t]*[:#\-=]?[ \t]*" + _ID_VALUE, re.IGNORECASE)

_OTHER_LABEL = _compile(
    r"(?<![A-Za-z])(?P<label>Policy|Claim|Insurance|TPA|Member(?:ship)?|Beneficiary|Ayushman"
    r"|Voter|EPIC|Passport|Driving[ \t]*Licen[cs]e|DL|Account|A/c|Ration[ \t]*Card|Card"
    r"|बीमा|पॉलिसी)(?:[ \t]*(?:ID|No|Number|Card|सं|संख्या|नं))*\.?[ \t]*[:#=\-][ \t]*" + _ID_VALUE,
    re.IGNORECASE)

# Government and programme IDs with a distinctive name need no separator ("Ration Card
# 4017…"). A 12-digit ration card or RCH number must not be read as an Aadhaar number.
_GOVT_ID_LABEL = _compile(
    r"(?<![A-Za-z])(?:Ration[ \t]*Card|Voter[ \t]*ID|EPIC|Driving[ \t]*Licen[cs]e|Passport"
    r"|MLC|FIR|RCH(?:[ \t]*ID)?|MCTS(?:[ \t]*ID)?|Nikshay(?:[ \t]*ID)?|ESIC|UDID|Family[ \t]*ID"
    r"|Vehicle|राशन[ \t]*कार्ड|मतदाता[ \t]*पहचान)(?:[ \t]*(?:No|Number|ID|सं|नं)\.?)?[ \t]*[:#=\-–]?"
    r"[ \t]*(?P<id>[A-Za-z0-9](?:[A-Za-z0-9/\-_.]|[ ](?=[A-Za-z0-9])){2,34}[0-9])", re.IGNORECASE)

_INSURANCE_LABELS = frozenset({"policy", "claim", "insurance", "tpa", "member", "membership",
                               "beneficiary", "ayushman"})

_AADHAAR_LABEL = _compile(
    r"(?:Aadhaa?r|Adhaa?r|UIDAI|आधार)[^\d\n]{0,25}?(?P<id>\d(?:[ \-.]?\d){7,13})(?!\d)",
    re.IGNORECASE)
_PHONE_LABEL = _compile(
    r"(?:(?<![A-Za-z])(?:Mob(?:ile)?|Phone|Ph|Contact|Tel|Cell|Whats[ \t]*App|Callback)"
    r"(?:[ \t]*(?:No|Number))?|मोबाइल|फ़ोन|फोन|दूरभाष)\.?[ \t]*[:\-=]?[ \t]*"
    r"(?P<id>(?:\+[ \t]?)?\d(?:[ \t\-.()]{0,2}\d){6,14})(?!\d)", re.IGNORECASE)


def _digit_count(value: str) -> int:
    return sum(ch.isdigit() for ch in value)


# --------------------------------------------------------- numbers under any label

# Mention.data of a hospital or reference number found by the generic rules below, so that
# the dataset pass (wards.anchors) can check that it belongs to one person.
NUMBER_LABEL = "number-label"


# A label ending in a number word, whatever comes before it: "Indoor Reg. No.:- 26/K/417".
_NUMBER_WORD = (r"(?:(?<![A-Za-z])(?:No|Nos|Num|Number|Nbr|ID|Regn|Reg|Registration)(?![A-Za-z])\.?"
                r"|(?:सं|संख्या|नं|क्रमांक)(?![\u0900-\u097f])\.?|#)")
_NUMBERED = _compile(
    rf"(?P<marker>{_NUMBER_WORD}(?:[ \t]*{_NUMBER_WORD})*)"
    r"[ \t]*(?:(?::-|[:#=\-–—])[ \t]*){0,2}"
    r"(?P<id>(?:[A-Z]{1,6}[ ](?=\d))?[A-Za-z0-9]+(?:[/\-_.\\][A-Za-z0-9]+){0,6})(?![A-Za-z0-9])",
    re.IGNORECASE)
# A number that carries its own label as a prefix: "IP/26/0417", "UHID-004417".
_PREFIXED = _compile(
    r"(?<![A-Za-z0-9/\-_.])(?:IPD|IP|UHID|MRN|MRD|CRN)(?:[\-/](?:[A-Z]{1,4}[\-/])?\d{1,6}){0,2}"
    r"[\-/](?!(?:19|20)\d\d(?![\-/\d]))\d{3,8}(?![A-Za-z0-9]|[\-/.]\d)")


_UNIT_AFTER = re.compile(r"[ \t]*(?:(?:mg|mcg|ml|gm|kg|iu|units?|mmhg|bpm)(?![A-Za-z])|%|/cumm"
                         r"|g/dl|mg/dl)", re.IGNORECASE)


def _numbered(text: str) -> list[tuple[E, int, int]]:
    """Identifiers after any label that ends in a number word."""
    found = []
    for m in _NUMBERED.finditer(text):
        marker, value = m.group("marker"), m.group("id")
        # "no 250 mg" is a negation, not a label: a bare lower-case word needs "." or ":"
        if marker in ("no", "num", "id", "reg") \
                and not text[m.end("marker"):m.start("id")].strip().startswith((":", "#")):
            continue
        if _digit_count(value) < 3 or reads_as_other(value) or _UNIT_AFTER.match(text, m.end()):
            continue
        words = words_before(text, m.start()) + label_words(marker)
        if foreign_number(words):
            continue
        if personal_label(words):
            found.append((E.MRN, m.start("id"), m.end("id")))
        # an unknown kind of number is removed only when it is shaped like an identifier
        elif _digit_count(value) >= 5 or not value.isdigit():
            found.append((E.OTHER_ID, m.start("id"), m.end("id")))
    return found


def detect_ids(text: str) -> list[Mention]:
    """Identifier mentions in view text. Overlaps are resolved by the caller."""
    found: list[Mention] = []

    def add(etype: E, start: int, end: int, priority: int, source: str) -> None:
        found.append(Mention(etype, start, end, text[start:end], priority=priority,
                             source=source))

    if any(ch.isdigit() for ch in text) or "@" in text:
        for etype, priority, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                add(etype, m.start(), m.end(), priority, "pattern")
        for m in _MRN_LABEL.finditer(text):
            if _digit_count(m.group("id")) >= 3:
                add(E.MRN, m.start("id"), m.end("id"), 92, "label")
        for m in _OTHER_LABEL.finditer(text):
            if _digit_count(m.group("id")) >= 4:
                insured = m.group("label").lower() in _INSURANCE_LABELS
                add(E.INSURANCE_ID if insured else E.OTHER_ID, m.start("id"), m.end("id"),
                    87, "label")
        for m in _GOVT_ID_LABEL.finditer(text):
            if _digit_count(m.group("id")) >= 3:
                add(E.OTHER_ID, m.start("id"), m.end("id"), 88, "label")
        for m in _AADHAAR_LABEL.finditer(text):
            add(E.AADHAAR, m.start("id"), m.end("id"), 84, "label")
        for m in _PHONE_LABEL.finditer(text):
            add(E.PHONE, m.start("id"), m.end("id"), 81, "label")
        # last and weakest, so that every more specific rule above names the value first
        for etype, start, end in _numbered(text):
            add(etype, start, end, 80 if etype is E.MRN else 59, "label")
            found[-1].data = NUMBER_LABEL
        for m in _PREFIXED.finditer(text):
            add(E.MRN, m.start(), m.end(), 90, "pattern")
    return found

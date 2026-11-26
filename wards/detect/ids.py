"""Identifier detectors: government and health IDs, phones, e-mail, hospital numbers."""

from __future__ import annotations

import re
import unicodedata

from wards.model import EntityType as E
from wards.model import Mention


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
        for m in _AADHAAR_LABEL.finditer(text):
            add(E.AADHAAR, m.start("id"), m.end("id"), 84, "label")
        for m in _PHONE_LABEL.finditer(text):
            add(E.PHONE, m.start("id"), m.end("id"), 81, "label")
    return found

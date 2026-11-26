"""Ages, dates and sex markers.

An age is only recognised with age context ("Age:", "52 Yrs/F", "97 yrs, male"). Durations
such as "DM2 x 8 yrs" or "8 yrs back" are clinical history and must stay exact.
"""

from __future__ import annotations

import re
import unicodedata

from wards.dates import find_dates
from wards.model import EntityType as E
from wards.model import Mention

MAX_AGE = 120


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(unicodedata.normalize("NFKC", pattern), flags)


_SEX = r"(?:Male|Female|MALE|FEMALE|male|female|M|F|पुरुष|महिला|स्त्री|Transgender|O)"
_UNIT = r"(?:[Yy](?:(?:rs?|ears?|RS?)\b)?\.?|वर्ष|साल)"

_AGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Age: 52 | Age/Sex: 52 | उम्र 40
    _compile(r"(?:(?<![A-Za-z])(?:[Aa]ge|AGE|Umar|umar|Umra|Aayu)"
             r"(?:[ \t]*(?:/|&|and)[ \t]*(?:[Ss]ex|SEX|[Gg]ender))?[ \t]*[:\-=][ \t]*"
             r"|(?:आयु|उम्र)(?:[ \t]*/[ \t]*लिंग)?[ \t]*[:\-=]?[ \t]*)(?P<age>\d{1,3})(?!\d)"),
    # 52 Yrs/F | 35/F | 48 Y / F
    _compile(r"(?<![\d./\-])(?P<age>\d{1,3})[ \t]*" + _UNIT + r"?[ \t]*/[ \t]*" + _SEX +
             r"(?![A-Za-z0-9])"),
    # 97 yrs, male | 45 years old | 45-year-old
    _compile(r"(?<![\d./\-])(?P<age>\d{1,3})[ \t\-]*(?:[Yy]ears?|[Yy]rs?)\.?"
             r"(?=[ \t]*,?[ \t]*(?:male|female|Male|Female)\b|[ \t\-]*old\b)"),
    _compile(r"(?<![\d./\-])(?P<age>\d{1,3})[ \t]*(?:y/o|yo)(?![A-Za-z])", re.IGNORECASE),
    _compile(r"(?<![\d./\-])(?P<age>\d{1,3})[ \t]*(?:वर्षीय|वर्षीया)"),
    _compile(r"(?<![A-Za-z])(?:aged|Aged)[ \t]+(?P<age>\d{1,3})(?!\d)"),
    _compile(r"(?<![A-Za-z])(?:[Uu]mar|[Uu]mra)[ \t]+(?P<age>\d{1,3})(?!\d)"),
    _compile(r"(?<![\d./\-])(?P<age>\d{1,3})[ \t]*(?:saal|sal|years?|साल|वर्ष)[ \t]+(?:ka|ki|ke|का|की|के)"
             r"[ \t]+(?:mahila|aurat|aadmi|purush|mareez|marij|ladka|ladki|bachcha|bachchi|महिला|पुरुष|"
             r"आदमी|औरत|मरीज़|मरीज|लड़का|लड़की|बच्चा|बच्ची)"),
)


def detect_ages(text: str) -> list[Mention]:
    found = []
    for pattern in _AGE_PATTERNS:
        for m in pattern.finditer(text):
            if int(m.group("age")) <= MAX_AGE:
                found.append(Mention(E.AGE, m.start("age"), m.end("age"), m.group("age"),
                                     priority=65, source="age"))
    return found


_DOB_CONTEXT = _compile(
    r"(?:(?<![A-Za-z])(?:D[./]?O[./]?B\.?|Date[ \t]+of[ \t]+Birth|Birth[ \t]*Date|Born(?:[ \t]+on)?)"
    r"|जन्म[ \t]*(?:तिथि|दिनांक|तारीख)?)[ \t]*(?:\([^)\n]*\))?[ \t]*[:\-=]?[ \t]*$",
    re.IGNORECASE)


def detect_dates(text: str, typed: bool = False, is_dob: bool = False) -> list[Mention]:
    found = []
    for match in find_dates(text, typed=typed):
        birth = is_dob or bool(_DOB_CONTEXT.search(text[max(0, match.start - 40):match.start]))
        etype = E.DOB if birth else E.DATE
        found.append(Mention(etype, match.start, match.end, text[match.start:match.end],
                             priority=76 if birth else 64, source="date", data=match))
    return found


_SEX_WORDS = {"m": "M", "male": "M", "पुरुष": "M", "f": "F", "female": "F", "महिला": "F",
              "स्त्री": "F"}
_SEX_IN_TEXT = _compile(
    r"(?:\d[ \t]*(?:[Yy](?:rs?|ears?)?\.?|वर्ष|साल)?[ \t]*/[ \t]*(?P<a>Male|Female|male|female|M|F|पुरुष|महिला)"
    r"(?![A-Za-z])|(?:[Yy]ears?|[Yy]rs?)\.?[ \t]*,[ \t]*(?P<b>male|female|Male|Female)\b"
    r"|(?:Sex|Gender|लिंग)[ \t]*[:\-][ \t]*(?P<c>Male|Female|male|female|M|F|पुरुष|महिला)(?![A-Za-z]))")


def normalize_sex(value: str) -> str:
    return _SEX_WORDS.get(unicodedata.normalize("NFKC", value).strip().lower(), "")


def sexes_in_text(text: str) -> set[str]:
    out = set()
    for m in _SEX_IN_TEXT.finditer(text):
        sex = normalize_sex(m.group("a") or m.group("b") or m.group("c"))
        if sex:
            out.add(sex)
    return out

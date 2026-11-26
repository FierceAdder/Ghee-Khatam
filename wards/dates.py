"""Find dates in many formats and write shifted dates back *in the same format*.

Numeric dates such as ``02/06/2026`` are ambiguous. The order (day-first or month-first)
is decided per file from the evidence available (see :func:`choose_order`), because a
wrong reading would corrupt hospital stay lengths.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

DMY, MDY = "dmy", "mdy"

_EN_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december")
_HI_MONTHS = (("जनवरी",), ("फ़रवरी", "फरवरी"), ("मार्च",), ("अप्रैल",), ("मई",), ("जून",),
              ("जुलाई",), ("अगस्त",), ("सितंबर", "सितम्बर"), ("अक्टूबर", "अक्तूबर"),
              ("नवंबर", "नवम्बर"), ("दिसंबर", "दिसम्बर"))


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


_MONTH_NUMBER: dict[str, int] = {}
for _index, _name in enumerate(_EN_MONTHS, start=1):
    _MONTH_NUMBER[_name] = _index
    _MONTH_NUMBER[_name[:3]] = _index
_MONTH_NUMBER["sept"] = 9
for _index, _forms in enumerate(_HI_MONTHS, start=1):
    for _form in _forms:
        _MONTH_NUMBER[_nfkc(_form)] = _index

_MONTH_WORD = "(?:" + "|".join(sorted(map(re.escape, _MONTH_NUMBER), key=len, reverse=True)) + ")"
_YEAR = r"(?:19|20)\d\d"
_SUFFIX = r"(?:st|nd|rd|th)"
_BEFORE = r"(?<![A-Za-z0-9/.\-])"

_ISO = re.compile(rf"{_BEFORE}(?P<y>{_YEAR})(?P<sep>[-/.])(?P<m>\d{{1,2}})(?P=sep)"
                  rf"(?P<d>\d{{1,2}})(?![0-9]|[/\-]\d)")
_NUMERIC = re.compile(rf"{_BEFORE}(?P<a>\d{{1,2}})(?P<sep>[-/.])(?P<b>\d{{1,2}})(?P=sep)"
                      rf"(?P<y>{_YEAR}|\d\d)(?![A-Za-z0-9]|[/\-.]\d)")
_DAY_MONTH = re.compile(
    rf"(?<![A-Za-z0-9/.])(?P<d>\d{{1,2}})(?P<suffix>{_SUFFIX})?(?P<gap>[ \t\-/]+(?:of[ \t]+)?)"
    rf"(?P<mon>{_MONTH_WORD})(?![A-Za-z])"
    rf"(?:\.?[ \t\-/,]+(?P<y>{_YEAR}|(?<=-)\d\d)(?![0-9]))?", re.IGNORECASE)
_MONTH_DAY = re.compile(
    rf"(?<![A-Za-z])(?P<mon>{_MONTH_WORD})\.?[ \t\-]+(?P<d>\d{{1,2}})(?P<suffix>{_SUFFIX})?"
    rf"(?![0-9A-Za-z]|[.:/]\d)(?:[ \t]*,?[ \t]*(?P<y>{_YEAR})(?![0-9]))?", re.IGNORECASE)
_COMPACT = re.compile(r"^\s*(?:(?P<y>(?:19|20)\d\d)(?P<m>\d\d)(?P<d>\d\d))\s*$")
_DATE_LABEL = re.compile(
    r"(?i)(?:date|dated|dob|visit|on|since|admitted|discharged|review|तारीख|दिनांक|तिथि)"
    r"[ \t]*(?:of[ \t]+\w+[ \t]*)?[:\-]?[ \t]*$")


@dataclass(frozen=True)
class DateMatch:
    """One date occurrence. Group spans are relative to ``start``."""

    start: int
    end: int
    text: str
    first: tuple[int, int] | None    # first numeric field of an ambiguous date
    second: tuple[int, int] | None
    day: tuple[int, int] | None
    month: tuple[int, int] | None
    year: tuple[int, int] | None
    suffix: tuple[int, int] | None = None

    @property
    def ambiguous_form(self) -> bool:
        return self.first is not None

    def _int(self, span: tuple[int, int]) -> int:
        return int(self.text[span[0]:span[1]])

    def evidence(self) -> str | None:
        """The order this date proves on its own, if any."""
        if self.first is None or self.second is None:
            return None
        a, b = self._int(self.first), self._int(self.second)
        if a > 12 >= b >= 1:
            return DMY
        if b > 12 >= a >= 1:
            return MDY
        return None

    def _roles(self, order: str) -> tuple[tuple[int, int], tuple[int, int]]:
        """``(day_span, month_span)`` under the given reading."""
        if self.first is None or self.second is None:
            assert self.day is not None and self.month is not None
            return self.day, self.month
        order = self.evidence() or order
        return (self.first, self.second) if order == DMY else (self.second, self.first)

    def resolve(self, order: str, default_year: int = 2000) -> dt.date | None:
        day_span, month_span = self._roles(order)
        month_text = self.text[month_span[0]:month_span[1]]
        month = int(month_text) if month_text.isdigit() else _MONTH_NUMBER.get(month_text.lower())
        if month is None:
            return None
        if self.year is None:
            year = default_year
        else:
            year = self._int(self.year)
            if self.year[1] - self.year[0] == 2:
                year += 2000 if year <= 49 else 1900
        try:
            return dt.date(year, month, self._int(day_span))
        except ValueError:
            return None

    def render(self, new: dt.date, order: str) -> str:
        day_span, month_span = self._roles(order)
        pieces: list[tuple[tuple[int, int], str]] = [
            (day_span, _number(new.day, day_span)),
        ]
        month_text = self.text[month_span[0]:month_span[1]]
        if month_text.isdigit():
            pieces.append((month_span, _number(new.month, month_span)))
        else:
            pieces.append((month_span, _month_like(month_text, new.month)))
        if self.year is not None:
            width = self.year[1] - self.year[0]
            pieces.append((self.year, f"{new.year % 100:02d}" if width == 2 else f"{new.year:04d}"))
        if self.suffix is not None:
            original = self.text[self.suffix[0]:self.suffix[1]]
            ending = _ordinal(new.day)
            pieces.append((self.suffix, ending.upper() if original.isupper() else ending))
        out = self.text
        for (low, high), value in sorted(pieces, reverse=True):
            out = out[:low] + value + out[high:]
        return out


def _number(value: int, span: tuple[int, int]) -> str:
    return f"{value:02d}" if span[1] - span[0] >= 2 else str(value)


def _month_like(original: str, month: int) -> str:
    if not original.isascii():
        return _nfkc(_HI_MONTHS[month - 1][0])
    full = _EN_MONTHS[month - 1]
    # "may" is both the full and the short form, so it keeps the long style only if
    # nothing shows the writer was abbreviating.
    word = full[:3] if len(original) <= 3 or original.lower() == "sept" else full
    if original.isupper():
        return word.upper()
    return word.capitalize() if original[0].isupper() else word


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _match(m: re.Match[str], **groups: str) -> DateMatch:
    base = m.start()

    def span(name: str) -> tuple[int, int] | None:
        if name not in m.re.groupindex or m.group(name) is None:
            return None
        return m.start(name) - base, m.end(name) - base

    fields = {key: span(value) for key, value in groups.items()}
    return DateMatch(start=base, end=m.end(), text=m.group(0),
                     first=fields.get("first"), second=fields.get("second"),
                     day=fields.get("day"), month=fields.get("month"),
                     year=fields.get("year"), suffix=fields.get("suffix"))


def find_dates(text: str, typed: bool = False) -> list[DateMatch]:
    """All plausible dates in ``text``. ``typed`` marks a known date column or key."""
    found: list[DateMatch] = []

    def add(candidate: DateMatch) -> None:
        if candidate.resolve(DMY, 2024) is None and candidate.resolve(MDY, 2024) is None:
            return
        if any(candidate.start < d.end and d.start < candidate.end for d in found):
            return
        found.append(candidate)

    for m in _ISO.finditer(text):
        add(_match(m, day="d", month="m", year="y"))
    for m in _NUMERIC.finditer(text):
        if len(m.group("y")) == 2 and not typed and not _short_year_context(text, m):
            continue
        add(_match(m, first="a", second="b", year="y"))
    for m in _DAY_MONTH.finditer(text):
        if m.group("y") is None and not _yearless_ok(text, m, typed):
            continue
        add(_match(m, day="d", month="mon", year="y", suffix="suffix"))
    for m in _MONTH_DAY.finditer(text):
        if m.group("y") is None and not _yearless_ok(text, m, typed):
            continue
        add(_match(m, day="d", month="mon", year="y", suffix="suffix"))
    if typed and not found:
        compact = _COMPACT.match(text)
        if compact:
            add(_match(compact, day="d", month="m", year="y"))
    return sorted(found, key=lambda d: d.start)


def _short_year_context(text: str, m: re.Match[str]) -> bool:
    """``d/m/yy`` is always a date; ``d.m.yy`` only in chat stamps or after a date label."""
    if m.group("sep") == "/":
        return True
    if m.group("sep") != ".":
        return False
    before = text[max(0, m.start() - 30):m.start()]
    after = text[m.end():m.end() + 10]
    return before.endswith("[") or bool(re.match(r",?\s*\d{1,2}:\d{2}", after)) \
        or bool(_DATE_LABEL.search(before))


def _yearless_ok(text: str, m: re.Match[str], typed: bool) -> bool:
    """"5 September" is a date; "5 may be needed" and "march 3 times" are not."""
    month = m.group("mon")
    if month.isascii():
        if not month[0].isupper():
            return False
        if month.lower() == "may":
            return typed or bool(_DATE_LABEL.search(text[max(0, m.start() - 30):m.start()]))
    return 1 <= int(m.group("d")) <= 31


# ------------------------------------------------------------------ order inference

_HINT_MDY = re.compile(r"(?i)\bmm\s*[-/.]\s*dd\s*[-/.]\s*yy")
_HINT_DMY = re.compile(r"(?i)\bdd\s*[-/.]\s*mm\s*[-/.]\s*yy")


def order_hint(text: str) -> str | None:
    """An explicit format note such as ``DOB (MM/DD/YYYY)``."""
    mdy, dmy = bool(_HINT_MDY.search(text)), bool(_HINT_DMY.search(text))
    if mdy != dmy:
        return MDY if mdy else DMY
    return None


def own_order(matches: Iterable[DateMatch]) -> str | None:
    votes = Counter(m.evidence() for m in matches if m.evidence())
    if not votes or votes[DMY] == votes[MDY]:
        return None
    return DMY if votes[DMY] > votes[MDY] else MDY


def choose_order(matches: Sequence[DateMatch], hint: str | None,
                 group_votes: Counter) -> str:
    """Hint, then this file's own evidence, then sibling files, then plausibility."""
    if hint:
        return hint
    own = own_order(matches)
    if own:
        return own
    if group_votes[DMY] != group_votes[MDY]:
        return DMY if group_votes[DMY] > group_votes[MDY] else MDY
    spans = {}
    for order in (DMY, MDY):
        # Only dotted dates are reliably day-first; slashes and dashes can be either.
        dates = [d for m in matches
                 if m.ambiguous_form and "." not in m.text and (d := m.resolve(order))
                 and d.year >= 2000]
        if len(dates) >= 2:
            spans[order] = (max(dates) - min(dates)).days
    if len(spans) == 2 and spans[MDY] * 3 < spans[DMY]:
        return MDY
    return DMY

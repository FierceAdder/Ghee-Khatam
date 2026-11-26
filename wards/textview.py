"""A normalised *matching view* of a string, with offsets back to the original.

Detectors never look at raw input. They look at the view, where disguises are undone:
zero-width characters removed, Indic digits turned into ASCII, compatibility characters
(NFKC) and Cyrillic/Greek look-alikes folded, and ``\\uXXXX``, HTML-entity and
percent-encoded sequences decoded. Every view character remembers which source slice it
came from, so a span found in the view is redacted in the untouched original.
"""

from __future__ import annotations

import html
import re
import unicodedata

_ZERO_WIDTH = frozenset("\u00ad͏\u061cᅟᅠ឴឵\u180e\u200b\u200c\u200d"
                        "\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff")

_HOMOGLYPHS = str.maketrans({
    # Cyrillic look-alikes
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    "ѕ": "s", "ј": "j", "ԁ": "d", "ԛ": "q", "ԝ": "w", "һ": "h", "к": "k",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S",
    # Greek look-alikes (letters with medical meaning such as alpha, beta, mu are left alone
    # in the original; folding here only affects matching)
    "ο": "o", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "υ": "u",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})

_ENCODED = re.compile(
    r"(?P<uesc>(?:\\u[0-9a-fA-F]{4})+)"
    r"|(?P<entity>&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z]{2,8});)"
    r"|(?P<percent>(?:%[0-9a-fA-F]{2})+)"
)


class TextView:
    __slots__ = ("source", "text", "_starts", "_ends")

    def __init__(self, source: str, text: str, starts: list[int] | None,
                 ends: list[int] | None) -> None:
        self.source = source
        self.text = text
        self._starts = starts
        self._ends = ends

    @classmethod
    def of(cls, source: str) -> "TextView":
        # Fast path: nothing to undo, so offsets are the identity.
        if source.isascii() and _ENCODED.search(source) is None:
            return cls(source, source, None, None)
        chars: list[str] = []
        starts: list[int] = []
        ends: list[int] = []

        def emit(piece: str, start: int, end: int) -> None:
            for char in _fold(piece):
                chars.append(char)
                starts.append(start)
                ends.append(end)

        position = 0
        for match in _ENCODED.finditer(source):
            for index in range(position, match.start()):
                emit(source[index], index, index + 1)
            decoded = _decode(match)
            if decoded is None:
                for index in range(match.start(), match.end()):
                    emit(source[index], index, index + 1)
            else:
                for piece, start, end in decoded:
                    emit(piece, start, end)
            position = match.end()
        for index in range(position, len(source)):
            emit(source[index], index, index + 1)
        text = "".join(chars)
        if text == source:
            return cls(source, source, None, None)
        return cls(source, text, starts, ends)

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        """Map a non-empty view span to the matching slice of the original string."""
        if not 0 <= start < end <= len(self.text):
            raise ValueError("span_out_of_range")
        if self._starts is None or self._ends is None:
            return start, end
        return self._starts[start], self._ends[end - 1]

    def source_text(self, start: int, end: int) -> str:
        low, high = self.source_span(start, end)
        return self.source[low:high]


def _fold(piece: str) -> str:
    out: list[str] = []
    for char in piece:
        if char in _ZERO_WIDTH:
            continue
        if char.isascii():
            out.append(char)
            continue
        if "\u0300" <= char <= "\u036f":
            continue                       # a combining accent typed after its letter
        digit = unicodedata.decimal(char, None)
        if digit is not None:
            out.append(str(digit))
            continue
        folded = unicodedata.normalize("NFKC", char).translate(_HOMOGLYPHS)
        out.append("".join(_unaccent(c) for c in folded))
    return "".join(out)


def _unaccent(char: str) -> str:
    """The plain Latin letter under an accented one; every other character unchanged."""
    if char.isascii():
        return char
    base = unicodedata.normalize("NFD", char)
    if len(base) > 1 and base[0].isascii() and base[0].isalpha() \
            and all("\u0300" <= mark <= "\u036f" for mark in base[1:]):
        return base[0]
    return char


def _decode(match: re.Match[str]) -> list[tuple[str, int, int]] | None:
    """Decode one encoded run into ``(character, source_start, source_end)`` triples."""
    start = match.start()
    token = match.group(0)
    if match.lastgroup == "entity":
        decoded = html.unescape(token)
        return None if decoded == token else [(decoded, start, match.end())]
    if match.lastgroup == "uesc":
        units = [int(token[i + 2:i + 6], 16) for i in range(0, len(token), 6)]
        triples: list[tuple[str, int, int]] = []
        index = 0
        while index < len(units):
            code, width = units[index], 1
            if 0xD800 <= code <= 0xDBFF and index + 1 < len(units) \
                    and 0xDC00 <= units[index + 1] <= 0xDFFF:
                code = 0x10000 + ((code - 0xD800) << 10) + (units[index + 1] - 0xDC00)
                width = 2
            char = "�" if 0xD800 <= code <= 0xDFFF else chr(code)
            triples.append((char, start + index * 6, start + (index + width) * 6))
            index += width
        return triples
    # percent encoding: bytes are UTF-8
    raw = bytes(int(token[i + 1:i + 3], 16) for i in range(0, len(token), 3))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    triples = []
    offset = start
    for char in text:
        width = len(char.encode("utf-8")) * 3
        triples.append((char, offset, offset + width))
        offset += width
    return triples

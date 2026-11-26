"""Form layouts in free text, read without knowing the label words.

Hospitals type forms as text: ``Rogi :- Lalthanzuala Ralte`` or ``Mauza    Sohangarh``.
When the label word is unknown, the firewall still sees the *shape*: a short label, a
separator, a value. Values under the same label are collected across the whole dataset,
and the label gets the field type those values have together (names, villages, IDs,
phones, PIN codes), exactly as an unknown CSV column does. Detection then treats values
under that label as typed fields.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator

from wards.columns import Context, classify_label, infer_from_values, normalize_label
from wards.lexicon import Lexicon
from wards.model import Channel, Document
from wards.model import FieldType as F

_CELLS = re.compile(r"[ \t]{3,}|[ \t]*\|[ \t]*")
_PAIR = re.compile(
    r"^(?P<label>[A-Za-zऀ-ॣॱ-ॿ][A-Za-zऀ-ॣॱ-ॿ .'/()&]"
    r"{0,30}?)[ \t]*(?P<sep>:-|:|[ \t]-[ \t]|–)[ \t]*(?P<value>\S.*)$")
# Only these kinds are acted on: dates and clinical values are found by other means.
USEFUL = frozenset({F.PERSON_NAME, F.MRN, F.PHONE, F.AADHAAR, F.ABHA_NUMBER, F.PAN, F.EMAIL,
                    F.PIN, F.VILLAGE, F.DOB})
MIN_VALUES = 3
_PRIORITY = 79


def pairs(text: str) -> Iterator[tuple[str, int, int]]:
    """``(label, value_start, value_end)`` for every "label: value" cell in ``text``."""
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        cells, cursor = [], 0
        for m in _CELLS.finditer(body):
            cells.append((cursor, m.start()))
            cursor = m.end()
        cells.append((cursor, len(body)))
        for start, end in cells:
            cell = body[start:end]
            m = _PAIR.match(cell)
            if m and len(m.group("label").split()) <= 4 and m.group("value").strip():
                value_start = start + m.start("value")
                value_end = start + len(cell.rstrip())
                yield m.group("label"), offset + value_start, offset + value_end
        # "Label     value" on its own line: two cells, the first one a label
        if len(cells) == 2 and not _PAIR.match(body[cells[0][0]:cells[0][1]]):
            label = body[cells[0][0]:cells[0][1]].strip()
            value = body[cells[1][0]:cells[1][1]]
            if label and value.strip() and not any(c.isdigit() for c in label) \
                    and len(label.split()) <= 3 and _PAIR.match(label + ": x"):
                yield label, offset + cells[1][0], offset + cells[1][1]
        offset += len(line)


class LayoutTypes:
    """Field types learned for unknown form labels across a dataset."""

    def __init__(self, types: dict[str, F] | None = None) -> None:
        self.types = types or {}

    @classmethod
    def learn(cls, docs: Iterable[Document], lexicon: Lexicon) -> "LayoutTypes":
        values: dict[str, list[str]] = defaultdict(list)
        for doc in docs:
            for segment in doc.segments:
                if segment.field is not F.TEXT or segment.channel is not Channel.CONTENT \
                        or segment.numeric:
                    continue
                text = segment.view.text
                for label, start, end in pairs(text):
                    if classify_label(label) is None:
                        values[normalize_label(label)].append(text[start:end])
        types = {}
        context = Context(identifiers=True, places=True)
        for label, found in values.items():
            if len(found) >= MIN_VALUES:
                kind = infer_from_values(found, lexicon, context)
                if kind in USEFUL:
                    types[label] = kind
        return cls(types)

    def typed_values(self, text: str) -> Iterator[tuple[F, int, int]]:
        if not self.types:
            return
        for label, start, end in pairs(text):
            kind = self.types.get(normalize_label(label))
            if kind is not None:
                yield kind, start, end


PRIORITY = _PRIORITY

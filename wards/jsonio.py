"""JSON reading and writing that keeps number literals exactly as written.

``json`` would silently rewrite ``0.80`` as ``0.8``. Lab values must survive unchanged,
so numbers are carried as :class:`JsonNumber` and written back verbatim. The nesting
depth is checked before parsing, which keeps behaviour identical across Python versions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonNumber:
    raw: str


def loads(text: str) -> Any:
    return json.loads(text, parse_int=JsonNumber, parse_float=JsonNumber,
                      parse_constant=JsonNumber)


_STRUCTURAL = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.DOTALL)


def depth(text: str) -> int:
    """Maximum bracket nesting, ignoring brackets inside strings. Iterative."""
    current = deepest = 0
    for match in _STRUCTURAL.finditer(text):
        token = match.group(0)
        if token in "[{":
            current += 1
            deepest = max(deepest, current)
        elif token in "]}":
            current -= 1
    return deepest


_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _string(value: str) -> str:
    # Lone surrogates cannot be encoded as UTF-8.
    return json.dumps(_SURROGATE.sub("�", value), ensure_ascii=False)


def dumps(value: Any, indent: str | None = None, spaced: bool = True) -> str:
    """Serialise a parsed tree. ``indent=None`` produces single-line output."""
    out: list[str] = []
    item_sep = ", " if spaced else ","
    key_sep = ": " if spaced else ":"

    def write(node: Any, level: int) -> None:
        if isinstance(node, str):
            out.append(_string(node))
        elif isinstance(node, JsonNumber):
            out.append(node.raw)
        elif node is True:
            out.append("true")
        elif node is False:
            out.append("false")
        elif node is None:
            out.append("null")
        elif isinstance(node, (dict, list)):
            is_object = isinstance(node, dict)
            opener, closer = ("{", "}") if is_object else ("[", "]")
            if not node:
                out.append(opener + closer)
                return
            out.append(opener)
            items = node.items() if is_object else enumerate(node)
            for index, (key, child) in enumerate(items):
                if index:
                    out.append("," if indent is not None else item_sep)
                if indent is not None:
                    out.append("\n" + indent * (level + 1))
                if is_object:
                    out.append(_string(key) + key_sep)
                write(child, level + 1)
            if indent is not None:
                out.append("\n" + indent * level)
            out.append(closer)
        else:
            raise TypeError("unsupported_json_node")

    write(value, 0)
    return "".join(out)


def detect_style(text: str) -> tuple[str | None, bool]:
    """Return ``(indent, spaced)`` so the output resembles the input layout."""
    match = re.search(r"\n([ \t]+)\S", text)
    if "\n" in text.strip() and match:
        return match.group(1), True
    head = text[:4000]
    return None, '": ' in head

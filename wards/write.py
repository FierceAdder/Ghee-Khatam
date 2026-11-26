"""Format-preserving writers, ``records.jsonl`` and ``report.json``.

CSV stays CSV with the same delimiter and shape, JSON keeps its structure, key order and
number literals, text keeps its lines. Everything is written as UTF-8.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from collections.abc import Sequence
from pathlib import Path

from wards import jsonio
from wards.documents import JsonObject
from wards.model import DocKind, Document, Segment

REPORT_SCHEMA = "raksha-report/1"


def final_value(segment: Segment) -> str:
    """The string to write for a segment, re-encoding hidden payloads."""
    if segment.raw is None:
        return segment.final
    if segment.output is None:
        return segment.raw                 # untouched: keep the exact original encoding
    if not segment.codec:
        return segment.output              # a placeholder that is already encoded
    return base64.b64encode(segment.output.encode("utf-8")).decode("ascii")


def _unique(values: Sequence[str], originals: Sequence[str]) -> list[str]:
    """Keep renamed headers and keys distinct without touching unchanged ones."""
    taken = {o for v, o in zip(values, originals, strict=True) if v == o}
    result = []
    for value, original in zip(values, originals, strict=True):
        if value != original:
            candidate, number = value, 1
            while candidate in taken:
                number += 1
                candidate = f"{value}_{number}"
            value = candidate
            taken.add(value)
        result.append(value)
    return result


def serialize(doc: Document) -> str:
    if doc.kind is DocKind.TEXT:
        return final_value(doc.payload)
    if doc.kind is DocKind.CSV:
        return _csv(doc)
    text = jsonio.dumps(_materialize(doc.payload), indent=doc.options.get("indent"),
                        spaced=doc.options.get("spaced", True))
    return text + ("\n" if doc.options.get("final_newline") else "")


def _csv(doc: Document) -> str:
    table = doc.payload
    header = _unique([final_value(s) for s in table[0]], [s.text for s in table[0]])
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=doc.options["delimiter"],
                        lineterminator=doc.options["newline"])
    writer.writerow(header)
    for row in table[1:]:
        writer.writerow([final_value(s) for s in row])
    return buffer.getvalue()


def _materialize(node: object) -> object:
    """Turn the segment skeleton back into plain JSON values."""
    if isinstance(node, JsonObject):
        kept = [(k, v) for k, v in node.entries
                if not (isinstance(v, Segment) and v.drop)]
        keys = _unique([final_value(k) for k, _ in kept], [k.text for k, _ in kept])
        return {key: _materialize(value) for key, (_, value) in zip(keys, kept, strict=True)}
    if isinstance(node, list):
        return [_materialize(child) for child in node]
    if isinstance(node, Segment):
        if node.numeric:
            return jsonio.JsonNumber(node.final)
        return final_value(node)
    return node


def write_text_file(path: Path, content: str | bytes) -> None:
    """Text is written as UTF-8. Bytes (staged ciphertext) are written as they are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, bytes) else content.encode("utf-8", errors="replace")
    with open(path, "wb") as handle:
        handle.write(data)


def write_records(path: Path, records: Sequence[dict]) -> None:
    lines = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    write_text_file(path, lines)


def build_report(files: Sequence[dict], counts: dict, entities: dict, seconds: float,
                 peak_rss_mb: float, status: str = "complete") -> dict:
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "files": list(files),
        "counts": counts,
        "entities": dict(sorted(entities.items())),
        "timing": {"seconds": round(seconds, 2)},
        "resources": {"peak_rss_mb": peak_rss_mb},
    }


def report_text(report: dict) -> str:
    return json.dumps(report, indent=2) + "\n"


def write_report(path: Path, report: dict) -> None:
    write_text_file(path, report_text(report))

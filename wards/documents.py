"""Turn a decoded input file into a :class:`Document` of editable segments.

The parser never changes content. It only decides where one record ends and the next
begins, and which segments belong to the file as a whole (metadata, headers, keys).
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass

from wards import jsonio
from wards.columns import (classify_fhir, classify_json_key, classify_label, context_of,
                           infer_from_values, is_relative_entry, is_schema_label, type_columns)
from wards.errors import QuarantineError
from wards.ingest import InputFile
from wards.lexicon import Lexicon
from wards.model import Channel, DocKind, Document, FieldType as F, Record, Segment

csv.field_size_limit(16 * 1024 * 1024)

_BASE64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}$")

# Key types whose presence shows that a JSON object describes a person.
_IDENTITY_TYPES = frozenset({
    F.PATIENT_NAME, F.FIRST_NAME, F.LAST_NAME, F.PERSON_NAME, F.NAME, F.RELATIVE_NAME, F.MRN,
    F.PHONE, F.AADHAAR, F.ABHA_NUMBER, F.ABHA_ADDRESS, F.PAN, F.EMAIL, F.INSURANCE_ID,
    F.DOB,
})


@dataclass
class JsonObject:
    """An object whose keys are editable segments, kept in input order."""

    entries: list[tuple[Segment, object]]


def parse(item: InputFile, lexicon: Lexicon) -> Document:
    if item.kind is None:
        raise QuarantineError("unsupported_format")
    doc = Document(rel=item.rel, sha256=item.sha256, kind=item.kind)
    if item.kind is DocKind.CSV:
        _parse_csv(doc, item, lexicon)
    elif item.kind is DocKind.TEXT:
        _parse_text(doc, item)
    else:
        _parse_json(doc, item, lexicon)
    return doc


def _label_segment(text: str, channel: Channel, lexicon: Lexicon,
                   context: str = "", parent: str = "") -> Segment:
    """A header cell or object key. Plain field names are left completely alone."""
    kind = F.CODE if is_schema_label(text, lexicon) else F.TEXT
    return Segment(text, field=kind, channel=channel, context=context, label=parent)


# --------------------------------------------------------------------------- text


def _parse_text(doc: Document, item: InputFile) -> None:
    record = doc.new_record("doc")
    doc.payload = doc.new_segment(Segment(item.text, field=F.TEXT), record)


# ---------------------------------------------------------------------------- CSV


def _parse_csv(doc: Document, item: InputFile, lexicon: Lexicon) -> None:
    try:
        rows = list(csv.reader(io.StringIO(item.text, newline=""), delimiter=item.delimiter))
    except csv.Error:
        raise QuarantineError("malformed_csv") from None
    if not rows:
        raise QuarantineError("empty_file")
    if "\r\n" in item.text:
        newline = "\r\n"
    elif "\r" in item.text and "\n" not in item.text:
        newline = "\r"
    else:
        newline = "\n"
    doc.options.update(delimiter=item.delimiter, newline=newline)

    header, body = rows[0], rows[1:]
    types = type_columns(header, body, lexicon)
    table = [[doc.new_segment(_label_segment(cell, Channel.HEADER, lexicon))
              for cell in header]]
    for index, row in enumerate(body):
        record = doc.new_record(f"row:{index}")
        segments = []
        for column, cell in enumerate(row):
            known = column < len(types)
            segment = Segment(cell, field=types[column] if known else F.TEXT,
                              label=header[column] if known else "")
            segments.append(doc.new_segment(segment, record))
        table.append(segments)
    doc.payload = table


# --------------------------------------------------------------------------- JSON


def _parse_json(doc: Document, item: InputFile, lexicon: Lexicon) -> None:
    text = item.text.strip()
    try:
        tree = jsonio.loads(text)
    except (ValueError, RecursionError):
        raise QuarantineError("malformed_json") from None
    indent, spaced = jsonio.detect_style(text)
    doc.options.update(indent=indent, spaced=spaced,
                       final_newline=item.text.endswith("\n"))

    if isinstance(tree, dict) and "resourceType" in tree:
        doc.kind = DocKind.FHIR
        record_paths: set[tuple] = {()}
        hints: dict[tuple[str, ...], F] = {}
    else:
        hints = _type_unknown_keys(tree, lexicon)
        record_paths = _find_records(tree, hints)
    doc.payload = _Builder(doc, record_paths, lexicon, hints).build(tree, (), (), None, "", {})


# What a key that the label vocabulary does not know may turn out to hold.
_OPEN_FIELDS = (F.TEXT, F.NAME)
_MAX_SAMPLE = 200


def _type_unknown_keys(tree: object, lexicon: Lexicon) -> dict[tuple[str, ...], F]:
    """Type values under unfamiliar keys by what the same key holds across the file."""
    values: dict[tuple[str, ...], list[str]] = defaultdict(list)

    def collect(node: object, keys: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                collect(child, keys + (key,))
        elif isinstance(node, list):
            for child in node:
                collect(child, keys)
        elif keys and isinstance(node, (str, jsonio.JsonNumber)):
            if len(values[keys]) < _MAX_SAMPLE:
                values[keys].append(node if isinstance(node, str) else node.raw)

    collect(tree, ())
    known = {keys: classify_json_key(keys) for keys in values}
    unknown = [keys for keys in values
               if known[keys] in _OPEN_FIELDS and classify_label(keys[-1]) is None]
    hints: dict[tuple[str, ...], F] = {}
    # Two passes: identifiers found in the first count as context for names in the second.
    for _ in range(2):
        context = context_of(hints.get(keys, kind) for keys, kind in known.items())
        for keys in unknown:
            kind = infer_from_values(values[keys], lexicon, context)
            if kind is not None:
                hints[keys] = F.NAME if kind is F.PERSON_NAME else kind
    # As in a table: if no key claims the patient, the first unclaimed name column does.
    claimed = {hints.get(keys, kind) for keys, kind in known.items()}
    if not claimed & {F.PATIENT_NAME, F.FIRST_NAME, F.LAST_NAME} \
            and context_of(claimed).identifiers:
        for keys in unknown:
            if hints.get(keys) is F.NAME:
                hints[keys] = F.PATIENT_NAME
                break
    return hints


def _escape_pointer(part: object) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _has_identity(node: object, hints: dict, keys: tuple[str, ...] = (),
                  depth: int = 0) -> bool:
    if depth > 5:
        return False
    if isinstance(node, dict):
        for key, value in node.items():
            path = keys + (key,)
            if isinstance(value, (dict, list)):
                if classify_label(key) is F.PATIENT_NAME and isinstance(value, dict):
                    return True
                if _has_identity(value, hints, path, depth + 1):
                    return True
            elif value not in (None, "") \
                    and hints.get(path, classify_json_key(path)) in _IDENTITY_TYPES:
                return True
    elif isinstance(node, list):
        return any(_has_identity(child, hints, keys, depth + 1) for child in node[:20])
    return False


def _find_records(tree: object, hints: dict) -> set[tuple]:
    """Paths of the JSON nodes that each describe one record. ``()`` means whole file."""
    if isinstance(tree, list):
        if any(isinstance(x, dict) and _has_identity(x, hints) for x in tree):
            return {(i,) for i, x in enumerate(tree) if isinstance(x, dict)}
        return {()}
    if not isinstance(tree, dict):
        return {()}

    # An object that names one person itself is a single record (e.g. a lab report whose
    # "panels" list belongs to that patient).
    own = {key: value for key, value in tree.items()
           if not (isinstance(value, list) and value and isinstance(value[0], dict))}
    for key, value in own.items():
        if isinstance(value, dict):
            if classify_label(key) is F.PATIENT_NAME and _has_identity(value, hints, (key,)):
                return {()}
        elif value not in (None, "") \
                and hints.get((key,), classify_json_key((key,))) in _IDENTITY_TYPES:
            return {()}

    found: set[tuple] = set()

    def search(node: dict, path: tuple, depth: int) -> None:
        keys = tuple(p for p in path if isinstance(p, str))
        for key, value in node.items():
            if isinstance(value, list) and any(isinstance(x, dict) for x in value):
                # hints are keyed by the full key path from the top of the file
                if any(isinstance(x, dict) and _has_identity(x, hints, keys + (key,))
                       for x in value):
                    found.update(path + (key, i) for i, x in enumerate(value)
                                 if isinstance(x, dict))
            elif isinstance(value, dict) and depth < 3:
                search(value, path + (key,), depth + 1)

    search(tree, (), 0)
    return found or {()}


class _Builder:
    def __init__(self, doc: Document, record_paths: set[tuple], lexicon: Lexicon,
                 hints: dict[tuple[str, ...], F]) -> None:
        self.hints = hints
        self.lexicon = lexicon
        self.doc = doc
        self.record_paths = record_paths

    def build(self, node: object, path: tuple, keys: tuple[str, ...],
              record: Record | None, resource: str, siblings: dict) -> object:
        if record is None and path in self.record_paths:
            loc = "/" + "/".join(_escape_pointer(p) for p in path) if path else "doc"
            record = self.doc.new_record(loc)

        if isinstance(node, dict):
            if self.doc.kind is DocKind.FHIR and isinstance(node.get("resourceType"), str):
                resource, keys = node["resourceType"], ()
            entries = []
            for key, value in node.items():
                key_segment = self.doc.new_segment(
                    _label_segment(key, Channel.KEY, self.lexicon, resource,
                                   keys[-1] if keys else ""), record)
                child = self.build(value, path + (key,), keys + (key,), record, resource, node)
                entries.append((key_segment, child))
            return JsonObject(entries)

        if isinstance(node, list):
            return [self.build(value, path + (i,), keys, record, resource, siblings)
                    for i, value in enumerate(node)]

        if isinstance(node, str):
            return self.doc.new_segment(self._string(node, keys, resource, siblings), record)

        if isinstance(node, jsonio.JsonNumber):
            segment = Segment(node.raw, field=self._field(keys, resource, siblings),
                              numeric=True, label=keys[-1] if keys else "", context=resource)
            if record is None:
                segment.channel = Channel.METADATA
            return self.doc.new_segment(segment, record)

        return node      # true, false, null

    def _field(self, keys: tuple[str, ...], resource: str, siblings: dict) -> F:
        if self.doc.kind is DocKind.FHIR:
            return classify_fhir(keys, resource, siblings)
        if is_relative_entry(keys, siblings):
            return F.RELATIVE_NAME
        kind = classify_json_key(keys)
        return self.hints.get(keys, kind) if kind in _OPEN_FIELDS else kind

    def _string(self, value: str, keys: tuple[str, ...], resource: str,
                siblings: dict) -> Segment:
        label = keys[-1] if keys else ""
        segment = Segment(value, field=self._field(keys, resource, siblings), label=label,
                          context=resource)
        if self.doc.kind is DocKind.FHIR and label in ("id", "reference", "fullUrl"):
            segment.constrained = True
        if any(k.startswith("_") or k.lower() in ("meta", "metadata") for k in keys[:-1]):
            segment.channel = Channel.METADATA

        content_type = siblings.get("contentType") or siblings.get("content_type") \
            or siblings.get("mimeType")
        is_attachment = bool(content_type) and label.lower() in ("data", "content", "payload",
                                                                 "base64")
        decoded = _decode_base64(value, strict=not is_attachment)
        if decoded is not None or is_attachment:
            segment.raw = value
        if is_attachment:
            textual = str(content_type).lower().startswith(("text/", "application/json",
                                                            "application/xml"))
            if textual and decoded is not None:
                segment.text, segment.codec = decoded, "base64"
                segment.field, segment.channel = F.TEXT, Channel.ENCODED
            else:
                # An image, PDF or unreadable payload cannot be cleaned.
                segment.field = F.ATTACHMENT
        elif decoded is not None:
            segment.text, segment.codec = decoded, "base64"
            segment.field, segment.channel = F.TEXT, Channel.ENCODED
        return segment


def _decode_base64(value: str, strict: bool) -> str | None:
    """Decoded text if ``value`` is a base64 payload holding readable text."""
    compact = "".join(value.split())
    if not compact or not _BASE64.match(compact):
        return None
    if strict and (len(compact) < 24 or len(compact) % 4):
        return None
    if strict and " " in value.strip():
        return None
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=None,
                               validate=True)
        text = raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not text or any(not (c.isprintable() or c in "\r\n\t") for c in text):
        return None
    return text

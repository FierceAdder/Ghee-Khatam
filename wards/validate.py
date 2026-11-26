"""Check that a finished file kept the shape of its input.

CSV files keep their row and column counts, text files their line breaks, JSON its nesting
and value types, and a FHIR bundle gains no structural fault it did not arrive with.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections import Counter
from xml.etree import ElementTree

from wards.documents import JsonObject
from wards.jsonio import JsonNumber
from wards.model import DocKind, Document, Segment

_FHIR_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
_FHIR_DATE = re.compile(
    r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01])(T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d+)?)?"
    r"(Z|[+\-]([01]\d|2[0-3]):[0-5]\d)?)?)?)?$")
_DATE_KEYS = frozenset({"birthDate", "date", "lastUpdated", "timestamp", "recordedDate",
                        "effectiveDateTime", "authoredOn", "performedDateTime", "start", "end",
                        "issued", "occurrenceDateTime", "onsetDateTime", "abatementDateTime"})


def shape(doc: Document) -> object:
    """A description of structure that ignores every string value."""
    if doc.kind is DocKind.CSV:
        return tuple(len(row) for row in doc.payload)
    if doc.kind is DocKind.TEXT:
        text = doc.payload.final
        return text.count("\n"), text.count("\r")
    return _json_shape(doc.payload)


def input_shape(doc: Document) -> object:
    """The shape the output must have: the input's, less any deliberately removed key."""
    if doc.kind is DocKind.TEXT:
        text = doc.payload.text
        return text.count("\n"), text.count("\r")
    return shape(doc)


def _json_shape(node: object) -> object:
    if isinstance(node, JsonObject):
        return ("object", tuple(_json_shape(v) for _, v in node.entries
                                if not (isinstance(v, Segment) and v.drop)))
    if isinstance(node, list):
        return ("array", tuple(_json_shape(v) for v in node))
    if isinstance(node, Segment):
        return "number" if node.numeric else "string"
    return type(node).__name__


def plain(node: object, original: bool) -> object:
    """The parsed JSON value of a skeleton, before or after redaction."""
    if isinstance(node, JsonObject):
        out = {}
        for index, (key, value) in enumerate(node.entries):
            if not original and isinstance(value, Segment) and value.drop:
                continue
            name = key.text if original else key.final
            out[name if name not in out else f"{name}\x00{index}"] = plain(value, original)
        return out
    if isinstance(node, list):
        return [plain(value, original) for value in node]
    if isinstance(node, Segment):
        text = node.text if original else node.final
        if node.numeric:
            return JsonNumber(text)
        if node.raw is not None:             # an encoded payload: judge its encoding
            if original or node.output is None:
                return node.raw
            if node.codec:
                return base64.b64encode(text.encode("utf-8")).decode("ascii")
        return text
    return node


def fhir_faults(bundle: object) -> Counter:
    """Structural faults, each described without any value from the file."""
    faults: Counter = Counter()
    if not isinstance(bundle, dict) or not isinstance(bundle.get("resourceType"), str):
        faults["no_resource_type"] += 1
        return faults
    entries = bundle.get("entry", [])
    resources = [bundle]
    full_urls: set[str] = set()
    local: set[str] = set()
    if bundle.get("resourceType") == "Bundle":
        if not isinstance(entries, list):
            faults["entry_not_a_list"] += 1
            entries = []
        for entry in entries:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict) \
                    or not isinstance(resource.get("resourceType"), str):
                faults["entry_without_resource"] += 1
                continue
            resources.append(resource)
            url = entry.get("fullUrl")
            if isinstance(url, str):
                if url in full_urls:
                    faults["duplicate_full_url"] += 1
                full_urls.add(url)
            if isinstance(resource.get("id"), str):
                local.add(f"{resource['resourceType']}/{resource['id']}")
    for resource in resources:
        identifier = resource.get("id")
        if identifier is not None and not (isinstance(identifier, str)
                                           and _FHIR_ID.match(identifier)):
            faults["bad_resource_id"] += 1

    def walk(node: object, key: str) -> None:
        if isinstance(node, dict):
            data, kind = node.get("data"), node.get("contentType")
            if isinstance(data, str) and isinstance(kind, str):
                try:
                    base64.b64decode(data, validate=True)
                except (binascii.Error, ValueError):
                    faults["bad_attachment_data"] += 1
            for name, child in node.items():
                walk(child, name)
            return
        if isinstance(node, list):
            for child in node:
                walk(child, key)
            return
        if not isinstance(node, str):
            return
        if node == "":
            faults["empty_string"] += 1
        elif key == "reference":
            if node.startswith("urn:") and node not in full_urls:
                faults["unresolved_reference"] += 1
            elif re.match(r"^[A-Z][A-Za-z]+/", node) and node not in local:
                faults["unresolved_reference"] += 1
        elif key in _DATE_KEYS and not _FHIR_DATE.match(node):
            faults[f"bad_date.{key}"] += 1
        elif key == "div":
            faults.update(_xhtml_faults(node))

    walk(bundle, "")
    return faults


def _xhtml_faults(div: str) -> Counter:
    # Declarations are refused outright so that no entity can ever be expanded.
    if "<!DOCTYPE" in div or "<!ENTITY" in div:
        return Counter({"narrative_declaration": 1})
    try:
        ElementTree.fromstring(div)
    except ElementTree.ParseError:
        return Counter({"narrative_not_xml": 1})
    return Counter()


def problems(source: Document, written: Document) -> list[str]:
    """Faults in ``written`` (re-read from disk) that redaction introduced."""
    found = []
    if source.kind is not written.kind:
        return ["format_changed"]
    if input_shape(source) != shape(written):
        found.append("shape_changed")
    if source.kind is DocKind.FHIR:
        before = fhir_faults(plain(source.payload, original=True))
        after = fhir_faults(plain(written.payload, original=True))
        if after - before:
            found.append("fhir_faults_added")
    return found

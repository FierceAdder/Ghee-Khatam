"""What the run log may contain: a closed list of events, fields and values.

The log is written into the release (``logs/run.jsonl``), which the scorer scans for
personal data, and it may be kept long after the data is gone. So nothing in it can come
from the input: every field is a number, a value from a fixed list below, a short hex
digest, or the class name of an exception. Files are named by their position in
``report.json``'s file list, never by path, because file names can hold names.

Anything outside this schema is refused (or, in a real run, dropped and counted).
"""

from __future__ import annotations

import re

from wards.model import EntityType

SUMMARY, DETAIL = "summary", "detail"

INT, FLOAT, HEX = "int", "float", "hex"
CLASS_NAME = re.compile(r"^[A-Z][A-Za-z0-9]{0,47}$")      # a Python exception class
_HEX = re.compile(r"^[0-9a-f]{1,64}$")

STAGES = frozenset({
    "ingest", "parse", "detect", "sweep", "model", "link", "conditions", "redact", "audit", "names",
    "stage", "publish", "run"})

# Every reason a file or a run can fail. A test checks that the code uses no other.
REASONS = frozenset({
    # ingest and parse
    "binary_content", "empty_file", "file_too_large", "malformed_csv", "malformed_json",
    "nesting_too_deep", "not_a_regular_file", "undecodable_text", "unreadable_file",
    "unsupported_format",
    # verification before release
    "fhir_faults_added", "format_changed", "self_scan_failed", "shape_changed",
    # anything else
    "processing_error", "run_failed", "terminated", "interrupted", "out_of_memory",
    "other"})

# Every detector that can produce a mention (``Mention.source``). Unknown ones count as other.
SOURCES = frozenset({
    "pattern", "label", "field", "path", "address", "date", "age", "embedded", "known",
    "sweep", "rare", "clipped", "gazetteer", "patient_label", "relative_label",
    "spoken_name", "patient_word", "relation", "relation_subject", "care_of", "role_after",
    "acted_by", "honorific", "title", "postposition", "possessive", "report_of",
    "before_age", "time_entry", "before_phone", "chat_sender", "staff_role", "staff_done_by",
    "layout", "model", "other"})

ENTITIES = frozenset(e.value for e in EntityType)

_COUNTS = ("files_total", "files_sanitized", "files_quarantined", "records_processed",
           "records_linked", "records_quarantined")
_LINKING = (
    "records", "linked", "patients", "ambiguous", "unanchored", "merged_strong",
    "merged_dob", "merged_relative", "merged_village", "merged_given", "refused_sex",
    "refused_aadhaar", "refused_abha", "refused_dob", "refused_age", "refused_name",
    "skipped_shared_phone", "skipped_conflict", "skipped_weak")

# event -> (level, {field: kind}); a kind is INT, FLOAT, HEX, CLASS_NAME or a set of values
EVENTS: dict[str, tuple[str, dict]] = {
    "run_started": (SUMMARY, {"files": INT, "policy": HEX}),
    "stage": (SUMMARY, {"stage": STAGES, "seconds": FLOAT, "peak_rss_mb": FLOAT,
                        "files": INT}),
    "linking": (SUMMARY, dict.fromkeys(_LINKING, INT)),
    "privacy": (SUMMARY, {"age_band_years": INT, "suppressed_codes": INT, "patients": INT,
                          "unique_band_sex_district": INT,
                          "patients_with_code_held_by_3_or_fewer": INT}),
    "self_scan": (SUMMARY, {"repairs": INT, "files_repaired": INT, "files_quarantined": INT,
                            "records_withheld": INT}),
    "problem": (SUMMARY, {"stage": STAGES, "reason": REASONS, "error": CLASS_NAME,
                          "file": INT}),
    "run_finished": (SUMMARY, {"status": frozenset({"complete", "failed"}), "seconds": FLOAT,
                               "peak_rss_mb": FLOAT, **dict.fromkeys(_COUNTS, INT)}),
    "file": (DETAIL, {"file": INT, "status": frozenset({"sanitized", "quarantined"}),
                      "reason": REASONS, "records": INT, "repairs": INT, "renamed": INT}),
    "file_entities": (DETAIL, {"file": INT, **dict.fromkeys(ENTITIES, INT)}),
    "file_sources": (DETAIL, {"file": INT, **dict.fromkeys(SOURCES, INT)}),
    "log": (SUMMARY, {"dropped": INT, "overflow": INT}),
    "name_model": (SUMMARY, {"files_skipped": INT}),
    "seal": (SUMMARY, {"lines": INT, "report": HEX}),
}


def check(event: str, fields: dict) -> str | None:
    """Why ``event`` with ``fields`` breaks the schema, or ``None`` if it is allowed."""
    spec = EVENTS.get(event)
    if spec is None:
        return "unknown_event"
    allowed = spec[1]
    for name, value in fields.items():
        kind = allowed.get(name)
        if kind is None:
            return "unknown_field"
        if not _fits(kind, value):
            return "bad_value"
    return None


def _fits(kind, value) -> bool:
    if isinstance(value, bool):
        return False
    if kind is INT:
        return isinstance(value, int)
    if kind is FLOAT:
        return isinstance(value, (int, float))
    if kind is HEX:
        return isinstance(value, str) and bool(_HEX.match(value))
    if kind is CLASS_NAME:
        return isinstance(value, str) and bool(CLASS_NAME.match(value))
    return isinstance(value, str) and value in kind


def level_of(event: str) -> str:
    return EVENTS[event][0]

"""Shared data model.

A :class:`Document` is made of :class:`Segment` objects (one editable string each: a CSV
cell, a JSON string/number/key, or a whole text note). Segments are grouped into
:class:`Record` objects, which are the unit of linkage and of ``records.jsonl``.
Detectors attach :class:`Mention` spans to segments; nothing edits text until redaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field  # Segment has an attribute called ``field``
from enum import Enum
from typing import Any

from wards.textview import TextView


class EntityType(str, Enum):
    PATIENT_NAME = "PATIENT_NAME"
    RELATIVE_NAME = "RELATIVE_NAME"
    PHONE = "PHONE"
    PHONE_MASKED = "PHONE_MASKED"
    AADHAAR = "AADHAAR"
    AADHAAR_MASKED = "AADHAAR_MASKED"
    ABHA_NUMBER = "ABHA_NUMBER"
    ABHA_ADDRESS = "ABHA_ADDRESS"
    PAN = "PAN"
    EMAIL = "EMAIL"
    MRN = "MRN"
    INSURANCE_ID = "INSURANCE_ID"
    OTHER_ID = "OTHER_ID"
    HOUSE = "HOUSE"
    LOCALITY = "LOCALITY"
    LANDMARK = "LANDMARK"
    VILLAGE = "VILLAGE"
    TEHSIL = "TEHSIL"
    PIN = "PIN"
    DOB = "DOB"
    DATE = "DATE"
    AGE = "AGE"


NAME_TYPES = frozenset({EntityType.PATIENT_NAME, EntityType.RELATIVE_NAME})
ID_TYPES = frozenset({
    EntityType.PHONE, EntityType.PHONE_MASKED, EntityType.AADHAAR, EntityType.AADHAAR_MASKED,
    EntityType.ABHA_NUMBER, EntityType.ABHA_ADDRESS, EntityType.PAN, EntityType.EMAIL,
    EntityType.MRN, EntityType.INSURANCE_ID, EntityType.OTHER_ID,
})
# Quasi-identifiers that are transformed rather than being identifiers in themselves.
QUASI_TYPES = frozenset({EntityType.DATE, EntityType.AGE})


class Role(str, Enum):
    PATIENT = "patient"
    RELATIVE = "relative"
    UNKNOWN = "unknown"


class FieldType(str, Enum):
    """What a column, key or segment is expected to hold."""

    TEXT = "text"                    # free text: run every detector
    PATIENT_NAME = "patient_name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    RELATIVE_NAME = "relative_name"
    PERSON_NAME = "person_name"      # a person who is not known to be the patient
    NAME = "name"                    # a "name" key whose owner is unknown
    DOCTOR_NAME = "doctor_name"
    PHONE = "phone"
    AADHAAR = "aadhaar"
    ABHA_NUMBER = "abha_number"
    ABHA_ADDRESS = "abha_address"
    PAN = "pan"
    EMAIL = "email"
    MRN = "mrn"
    INSURANCE_ID = "insurance_id"
    DOB = "dob"
    DATE = "date"
    AGE = "age"
    AGE_SEX = "age_sex"
    SEX = "sex"
    ADDRESS = "address"
    HOUSE = "house"
    LOCALITY = "locality"
    LANDMARK = "landmark"
    VILLAGE = "village"
    TEHSIL = "tehsil"
    DISTRICT = "district"
    STATE = "state"
    PIN = "pin"
    CLINICAL = "clinical"            # medicines, results, diagnoses: never name-matched
    ORG = "org"                      # hospital, lab or department names
    CODE = "code"                    # machine codes, URLs, UUIDs
    PATH = "path"                    # file system paths inside metadata
    ATTACHMENT = "attachment"        # binary attachment that cannot be inspected


class Channel(str, Enum):
    CONTENT = "content"
    HEADER = "header"
    KEY = "key"
    METADATA = "metadata"
    ENCODED = "encoded"


class DocKind(str, Enum):
    CSV = "csv"
    JSON = "json"
    FHIR = "fhir"
    TEXT = "text"


@dataclass(eq=False)
class Mention:
    """A span of personal data inside a segment, in *view* coordinates."""

    etype: EntityType
    start: int
    end: int
    value: str
    role: Role = Role.UNKNOWN
    priority: int = 50
    source: str = ""
    data: Any = None                 # detector specific payload, e.g. a DateMatch

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


@dataclass(eq=False)
class Segment:
    text: str
    field: FieldType = FieldType.TEXT
    channel: Channel = Channel.CONTENT
    label: str = ""
    numeric: bool = False            # a JSON number: the replacement must stay a number
    codec: str = ""                  # "base64" when ``text`` is the decoded payload
    record: "Record | None" = None
    mentions: list[Mention] = dc_field(default_factory=list)
    output: str | None = None        # final value; None means unchanged
    drop: bool = False               # remove the JSON key that holds this value
    context: str = ""                # structural hint, e.g. the FHIR resource type
    raw: str | None = None           # original encoded value when ``codec`` is set
    constrained: bool = False        # a FHIR id/reference: only [A-Za-z0-9.-] allowed
    _view: TextView | None = dc_field(default=None, repr=False)

    @property
    def view(self) -> TextView:
        if self._view is None:
            self._view = TextView.of(self.text)
        return self._view

    @property
    def final(self) -> str:
        return self.text if self.output is None else self.output


@dataclass(eq=False)
class Record:
    loc: str
    doc: "Document"
    segments: list[Segment] = dc_field(default_factory=list)
    patient: str | None = None
    generalized: bool = False

    @property
    def key(self) -> str:
        """Stable internal identity used for deterministic ordering and keyed offsets."""
        return f"{self.doc.rel}\x00{self.loc}"

    def add(self, segment: Segment) -> Segment:
        segment.record = self
        self.segments.append(segment)
        return segment

    def mentions(self):
        for segment in self.segments:
            for mention in segment.mentions:
                yield segment, mention


@dataclass(eq=False)
class Document:
    rel: str
    sha256: str
    kind: DocKind
    records: list[Record] = dc_field(default_factory=list)
    segments: list[Segment] = dc_field(default_factory=list)   # every segment, in order
    payload: Any = None              # format specific skeleton used by the writer
    options: dict[str, Any] = dc_field(default_factory=dict)

    def new_record(self, loc: str) -> Record:
        record = Record(loc=loc, doc=self)
        self.records.append(record)
        return record

    def new_segment(self, segment: Segment, record: Record | None = None) -> Segment:
        self.segments.append(segment)
        if record is not None:
            record.add(segment)
        return segment

    def file_segments(self):
        return (s for s in self.segments if s.record is None)

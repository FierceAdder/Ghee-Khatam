"""Replace every mention: pseudonyms, typed tokens and generalised values.

Replacements keep the value's type (a numeric Aadhaar becomes the number 0), never add or
remove CSV columns or JSON keys (except a FHIR birth date that must go), and never change
a text file's line count.
"""

from __future__ import annotations

import base64
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from wards.clinical import CODE_REPLACEMENT, NAME_REPLACEMENT
from wards.knowledge import Knowledge
from wards.lexicon import Lexicon
from wards.link import name_tokens
from wards.model import NAME_TYPES, Channel, Mention, Record, Role, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.normalize import is_devanagari
from wards.privacy import Privacy
from wards.sweep import NameBag

TOKENS = {
    E.PATIENT_NAME: "[NAME]", E.RELATIVE_NAME: "[RELATIVE]", E.PHONE: "[PHONE]",
    E.PHONE_MASKED: "[PHONE]", E.AADHAAR: "[AADHAAR]", E.AADHAAR_MASKED: "[AADHAAR]",
    E.ABHA_NUMBER: "[ABHA]", E.ABHA_ADDRESS: "[ABHA]", E.PAN: "[PAN]", E.EMAIL: "[EMAIL]",
    E.MRN: "[ID]", E.INSURANCE_ID: "[ID]", E.OTHER_ID: "[ID]", E.HOUSE: "[HOUSE]",
    E.LOCALITY: "[LOCALITY]", E.LANDMARK: "[LANDMARK]", E.VILLAGE: "[VILLAGE]",
    E.TEHSIL: "[TEHSIL]", E.PIN: "[PIN]", E.DOB: "[DOB]", E.DATE: "[DATE]", E.AGE: "[AGE]",
}
STAFF_TOKEN = "[STAFF]"
ATTACHMENT_PLACEHOLDER = base64.b64encode(b"[ATTACHMENT REMOVED]").decode("ascii")
_GENERALIZING = frozenset({E.AGE, E.DOB, E.VILLAGE, E.TEHSIL, E.PIN, E.DIAGNOSIS})
_DIGIT_PRESERVING = frozenset({E.AGE, E.DATE, E.DOB, E.PIN})
# Where the pseudonym goes when a record has no patient name to replace.
_CARRIERS = (E.PATIENT_NAME, E.MRN, E.AADHAAR, E.ABHA_NUMBER, E.INSURANCE_ID, E.PHONE,
             E.EMAIL, E.PAN)


def _in_script_of(original: str, value: str) -> str:
    """Use Devanagari (or other Indic) digits if the replaced text used them."""
    for char in original:
        if not char.isascii() and unicodedata.decimal(char, None) is not None:
            zero = ord(char) - unicodedata.decimal(char)
            return "".join(chr(zero + int(c)) if c in "0123456789" else c for c in value)
    return value


@dataclass
class PatientContext:
    """What redaction needs to know about one record."""

    pseudonym: str | None
    offset: int
    order: str
    reference_year: int
    top_coded: bool = False
    bag: NameBag = field(default_factory=NameBag)
    district: object | None = None
    shift_dates: bool = True
    birth_dates: frozenset = frozenset()


class Redactor:
    def __init__(self, lexicon: Lexicon, privacy: Privacy, knowledge: Knowledge) -> None:
        self.lexicon = lexicon
        self.privacy = privacy
        self.knowledge = knowledge
        self.counts: Counter = Counter()

    def record(self, record: Record, context: PatientContext) -> None:
        for segment in record.segments:
            self.segment(segment, context)
        record.patient = context.pseudonym
        if context.pseudonym and not self._carries(record, context.pseudonym):
            record.patient = self._place_pseudonym(record, context)
        record.generalized = any(m.etype in _GENERALIZING for _, m in record.mentions())

    def segment(self, segment: Segment, context: PatientContext,
                forced: Mention | None = None) -> None:
        if not segment.mentions:
            return
        if segment.field is F.ATTACHMENT:
            segment.output = ATTACHMENT_PLACEHOLDER
            self.counts[E.OTHER_ID.value] += 1
            return
        if segment.numeric:
            self._number(segment, segment.mentions[0], context)
            return
        pieces: list[tuple[int, int, str]] = []
        for mention in segment.mentions:
            if mention is forced:
                value: str | None = context.pseudonym
            else:
                value = self._replacement(segment, mention, context)
            if value is None:
                continue
            start, end = segment.view.source_span(mention.start, mention.end)
            original = segment.text[start:end]
            if mention.etype in _DIGIT_PRESERVING:
                value = _in_script_of(original, value)
            if value != original:
                # a replacement must never join or split lines
                value += "".join(c for c in original if c in "\r\n")
                pieces.append((start, end, value))
        if forced is None:
            self.counts.update(m.etype.value for m in segment.mentions)
        if not pieces:
            return
        output, cursor = [], 0
        for start, end, value in sorted(pieces):
            if start < cursor:       # two view spans decoded from one source sequence
                continue
            output.append(segment.text[cursor:start])
            output.append(value)
            cursor = end
        output.append(segment.text[cursor:])
        segment.output = "".join(output)

    # ------------------------------------------------------------- replacements

    def _replacement(self, segment: Segment, mention: Mention,
                     context: PatientContext) -> str | None:
        etype = mention.etype
        if etype is E.DIAGNOSIS:
            return CODE_REPLACEMENT if mention.data == "code" else NAME_REPLACEMENT
        if segment.constrained:
            return "redacted"
        if etype in NAME_TYPES:
            return self._name(mention, context)
        if etype is E.DATE and self._is_birth_date(mention, context):
            etype = E.DOB
        if etype is E.DATE:
            if not context.shift_dates:
                return None
            shifted = self.privacy.shifted(mention.data, context.order, context.offset,
                                           context.reference_year) if mention.data else None
            return shifted if shifted is not None else TOKENS[E.DATE]
        if etype is E.DOB:
            value = self.privacy.date_of_birth(mention.data, context.order, context.offset,
                                               context.reference_year, context.top_coded)
            if value is None and segment.label == "birthDate":
                segment.drop = True        # FHIR dates cannot hold a placeholder
            return value if value is not None else TOKENS[E.DOB]
        if etype is E.AGE:
            if mention.data == "infant":
                return self.privacy.age_band(0)
            if not mention.value.isdigit():
                return TOKENS[E.AGE]
            return self.privacy.age_band(int(mention.value))
        if etype is E.PIN:
            return self.privacy.pin_prefix(mention.value)
        if etype is E.VILLAGE:
            return self._village(mention, context)
        return TOKENS[etype]

    @staticmethod
    def _is_birth_date(mention: Mention, context: PatientContext) -> bool:
        """An unlabelled date equal to the patient's known birth date.

        Recent dates are excluded: a newborn's admission falls on the birth date, and that
        visit date has to keep moving with the rest of the stay.
        """
        if mention.data is None or not context.birth_dates:
            return False
        value = mention.data.resolve(context.order, context.reference_year)
        return value in context.birth_dates and value.year <= context.reference_year - 2

    def _name(self, mention: Mention, context: PatientContext) -> str:
        keys = {self.lexicon.name_key(w) for w in name_tokens(mention.value, self.lexicon)}
        bag = context.bag
        if mention.role is Role.STAFF:
            return STAFF_TOKEN
        if mention.role is Role.RELATIVE:
            return TOKENS[E.RELATIVE_NAME]
        if keys & bag.patient or (mention.role is Role.PATIENT and not bag.patient):
            return context.pseudonym or TOKENS[E.PATIENT_NAME]
        if keys and keys <= bag.relative:
            return TOKENS[E.RELATIVE_NAME]
        return TOKENS[E.PATIENT_NAME]

    def _village(self, mention: Mention, context: PatientContext) -> str:
        """Generalise a village to its district, in the script it was written in."""
        district = context.district
        if district is None:
            name = self.knowledge.district_of(mention.value)
            district = self.lexicon.district(name) if name else None
        if district is None:
            return TOKENS[E.VILLAGE]
        value = district.devanagari if is_devanagari(mention.value) else district.latin
        # never hand back text that still contains the village
        return TOKENS[E.VILLAGE] if mention.value.casefold() in value.casefold() else value

    def _number(self, segment: Segment, mention: Mention, context: PatientContext) -> None:
        etype = mention.etype
        self.counts[etype.value] += 1
        if etype is E.AGE:
            segment.output = self._replacement(segment, mention, context)
            segment.numeric = False          # a band is text (a declared generalisable path)
        elif etype is E.PIN:
            segment.output = self.privacy.pin_number(mention.value)
        elif etype in (E.DATE, E.DOB):
            value = self._replacement(segment, mention, context)
            if value is not None:       # None: dates of this record are not shifted
                segment.output = value if value.isdigit() else "0"
        else:
            segment.output = "0"

    # ---------------------------------------------------------------- pseudonym

    @staticmethod
    def _carries(record: Record, pseudonym: str) -> bool:
        return any(s.channel is not Channel.KEY and not s.drop and pseudonym in s.final
                   for s in record.segments)

    def _place_pseudonym(self, record: Record, context: PatientContext) -> str | None:
        """Write the pseudonym over the best patient identifier, or give up the claim."""
        for etype in _CARRIERS:
            for segment, mention in record.mentions():
                if mention.etype is etype and not segment.numeric and not segment.drop \
                        and segment.channel is not Channel.KEY \
                        and segment.field not in (F.ATTACHMENT, F.PATH) \
                        and mention.role is not Role.RELATIVE \
                        and not segment.constrained:
                    segment.output = None
                    self.segment(segment, context, forced=mention)
                    if self._carries(record, context.pseudonym):
                        return context.pseudonym
        return None

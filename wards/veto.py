"""The medical veto: spans that look personal but must survive.

The veto runs last and can only *remove* a detection, never add one. It protects doctors,
hospitals, disease eponyms, the ASHA worker role, doses and machine identifiers.
"""

from __future__ import annotations

import re
import unicodedata

from wards.detect.tokens import Tokens
from wards.lexicon import Lexicon
from wards.model import NAME_TYPES, Mention


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(unicodedata.normalize("NFKC", pattern), flags)


_DOCTOR = _compile(
    r"(?:(?<![A-Za-z])(?:Dr|DR|Prof|Doctor|DOCTOR)(?![A-Za-z])\.?|डॉक्टर|डॉ\.?|डा\.)[ \t]*"
    r"(?:\([^)\n]{0,12}\)[ \t]*)?")
_FACILITY_WORD = _compile(
    r"(?<![A-Za-z])(?:Hospital|Hospitals|Clinic|Sadan|Diagnostics|Diagnostic|Pathology|Pathlabs|"
    r"Labs|Lab|Laboratory|Laboratories|Institute|Centre|Center|Nursing[ \t]+Home|Trust|College|"
    r"Sansthan|Chikitsalaya|Dispensary|Imaging|Pharmacy|HOSPITAL|CLINIC|SADAN|DIAGNOSTICS|"
    r"PATHOLOGY|LABS|LAB|अस्पताल|चिकित्सालय|क्लिनिक|सदन)(?![A-Za-z\u0900-\u097f])")
_PLACE_WORD = _compile(
    r"(?<![A-Za-z])(?:Mandir|Masjid|Gurudwara|Gurdwara|Church|School|Chowk|Tanki|Bhawan|Bhavan|"
    r"Temple|Mosque|मंदिर|मस्जिद|गुरुद्वार[ाे]|भवन)(?![A-Za-z\u0900-\u097f])")
_PREVIOUS_WORD = _compile(r"([A-Z][A-Za-z.'\-]*|[\u0900-\u0963\u0971-\u097f]+)[ \t]$")

_DEGREE = r"(?:MBBS|M\.B\.B\.S\.?|MD|M\.D\.|MS|M\.S\.|DM|DNB|MCh|BAMS|BHMS|BDS|MDS|DGO|DCH|DPM|DCP|FRCS|MRCP)"
_QUALIFICATION = _compile(
    r"\([ \t]*" + _DEGREE + r"(?![A-Za-z])[^)\n]{0,60}\)"
    r"|(?<![A-Za-z])" + _DEGREE + r"(?=[ \t]+(?:Medicine|Surgery|General|Gen|Pediatrics|Paediatrics|"
    r"Pathology|Pulmonary|Obstetrics|Orthopaedics|Orthopedics|Radiology|Anaesthesia))")
_EPONYM = re.compile(
    r"(?<![A-Za-z])[A-Z][a-z]+(?:[\- ][A-Z][a-z]+)?(?:['’]s)?[ \t]+"
    r"(?i:disease|syndrome|palsy|lymphoma|sarcoma|thyroiditis|contracture|chorea|an[ae]emia|"
    r"phenomenon|tumou?r|fracture|sign)(?![A-Za-z])")
_ASHA = _compile(
    r"(?<![A-Za-z])(?:ASHA(?=[ \t]+(?i:worker|workers|didi|karyakarta|sahyogini|sangini|bahu|facilitator))"
    r"|(?i:asha)(?=[ \t]+(?i:worker|workers|karyakarta|sahyogini|sangini))"
    r"|(?:(?<=\bby )|(?<=\bthe )|(?<=\ban )|(?<=\blocal ))ASHA(?![A-Za-z])|आशा(?=[ \t]+(?:कार्यकर्ता|वर्कर|सहयोगिनी|संगिनी)))")
_ABHA_LABEL = _compile(r"(?<![A-Za-z])ABHA(?![A-Za-z])")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DOSE = _compile(
    r"(?<![A-Za-z0-9/])\d+(?:\.\d+)?[ \t]?(?:mg|mcg|µg|μg|ug|gm|g|ml|mL|IU|units?|puffs?|drops?)"
    r"(?![A-Za-z])")
_FREQUENCY = re.compile(r"(?<![\d\-])[0-4½](?:\.5)?(?:-[0-4½](?:\.5)?){2,3}(?![\d\-])")


# Detectors whose context states outright that a person is meant.
_EXPLICIT_PERSON = frozenset({"patient_label", "relative_label", "relation", "relation_subject",
                              "staff_role", "staff_done_by",
                              "care_of", "chat_sender", "report_of", "address"})


class Veto:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon

    def name_spans(self, text: str, tokens: Tokens | None = None) -> list[tuple[int, int]]:
        """Spans in which nothing may be treated as a patient or relative name."""
        return self._person_spans(text, tokens) + self._place_spans(text)

    def _place_spans(self, text: str) -> list[tuple[int, int]]:
        """Facility and landmark names ("Shanti Memorial Hospital")."""
        spans = []
        for pattern in (_FACILITY_WORD, _PLACE_WORD):
            for m in pattern.finditer(text):
                start = m.start()
                for _ in range(5):
                    previous = _PREVIOUS_WORD.search(text, max(0, start - 30), start)
                    if not previous:
                        break
                    start = previous.start(1)
                if start < m.start():
                    spans.append((start, m.end()))
        return spans

    def _person_spans(self, text: str, tokens: Tokens | None = None) -> list[tuple[int, int]]:
        """Doctors, eponyms, the ASHA role and qualifications."""
        spans = []
        doctors = list(_DOCTOR.finditer(text))
        if doctors:
            tokens = tokens or Tokens(text, self.lexicon)
            for m in doctors:
                index = tokens.index_at_or_after(m.end())
                if index < len(tokens) and tokens[index].start == m.end():
                    run = self._doctor_run(tokens, index)
                    if run:
                        spans.append((m.start(), tokens[run[-1]].end))
        for pattern in (_EPONYM, _ASHA, _ABHA_LABEL, _QUALIFICATION):
            spans.extend(m.span() for m in pattern.finditer(text))
        return spans

    @staticmethod
    def _doctor_run(tokens: Tokens, index: int) -> list[int]:
        """The name after "Dr.", initials included."""
        run: list[int] = []
        while index < len(tokens) and len(run) < 4:
            token = tokens[index]
            capital = token.deva or token.text[0].isupper()
            if not capital or (len(token.text) > 1 and tokens.lexicon.is_stopword(token.text)):
                break
            if run and not re.fullmatch(r"\.? ?|-", tokens.text[tokens[run[-1]].end:token.start]):
                break
            run.append(index)
            index += 1
        return run

    @staticmethod
    def value_spans(text: str) -> list[tuple[int, int]]:
        """Spans that can never be an identifier, date or age."""
        return [m.span() for pattern in (_UUID, _DOSE, _FREQUENCY)
                for m in pattern.finditer(text)]

    def apply(self, text: str, mentions: list[Mention],
              tokens: Tokens | None = None) -> list[Mention]:
        if not mentions:
            return mentions
        people = places = None
        values = self.value_spans(text)
        kept = []
        for mention in mentions:
            if mention.source == "field":
                kept.append(mention)
                continue
            if mention.etype in NAME_TYPES:
                if people is None:
                    people = self._person_spans(text, tokens)
                    places = self._place_spans(text)
                blocked = people
                if mention.source not in _EXPLICIT_PERSON:
                    blocked = people + places
            else:
                blocked = values
            if not any(mention.overlaps(start, end) for start, end in blocked):
                kept.append(mention)
        return kept

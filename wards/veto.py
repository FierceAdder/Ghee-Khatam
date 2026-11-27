"""The medical veto: spans that look personal but must survive.

The veto runs last and can only *remove* a detection, never add one. It protects doctors,
hospitals, disease eponyms, the ASHA worker and other hospital roles, doses and machine
identifiers.
"""

from __future__ import annotations

import re
import unicodedata

from wards.detect.tokens import Tokens
from wards.lexicon import DEPARTMENT_WORDS, ROLE_WORDS, Lexicon
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
# A degree written as a degree: two or more together, one no name is spelt like, or one set
# off after a name. "Md Danish" is none of these.
_ONLY_DEGREE = (r"(?:MBBS|M\.B\.B\.S\.?|M\.D(?!\.?[ \t]*[A-Z][a-z])\.?|D\.M\.|MCh|M\.Ch\.?|DNB|DGO|DCH|FRCS|MRCP|PhD|Ph\.D\.?|"
                r"एम\.[ \t]?बी\.[ \t]?बी\.[ \t]?एस\.?|एम\.[ \t]?डी\.?|एम\.[ \t]?एस\.?|डी\.[ \t]?एम\.?)")
_ANY_DEGREE = rf"(?:{_ONLY_DEGREE}|MD|MS|DM|MDS|BDS|BAMS|BHMS|DPM|DCP)"
_EDGE = r"(?<![A-Za-z\u0900-\u097f])"
_END = r"(?![A-Za-z\u0900-\u097f])"
_LISTED = r"[ \t]*[,/&][ \t]*|[ \t]+"
_DEGREES = _compile(
    rf"{_EDGE}{_ANY_DEGREE}{_END}(?:(?:{_LISTED}){_ANY_DEGREE}{_END})+"
    rf"|{_EDGE}{_ONLY_DEGREE}{_END}"
    rf"|(?:(?<=, )|(?<=,)){_ANY_DEGREE}(?=[ \t]*(?:[,;(|]|\.?[ \t]*$))"
    rf"|(?:(?<=[a-z] ))(?:MD|DM)(?=[ \t]*(?:[,;(|]|\.?[ \t]*$))", re.MULTILINE)
_NAME_WORD = r"(?:[A-Z][A-Za-z'\-]*\.?|[\u0900-\u0963\u0971-\u097f]+\.?)"
# The person those degrees belong to, with or without "Dr.".
_DEGREE_HOLDER = _compile(
    rf"{_EDGE}{_NAME_WORD}(?:[ \t]{_NAME_WORD}){{0,3}}[ \t]*,?[ \t]*(?=(?:{_ONLY_DEGREE}{_END}"
    rf"|{_ANY_DEGREE}{_END}(?:{_LISTED}){_ANY_DEGREE}{_END}))")
# "Inj <drug> 200 mg IV": after a dosage form, the words before a dose, unit, frequency or
# route are the medicine, whether or not any list holds it.
_DOSAGE_FORM = _compile(
    r"(?<![A-Za-z])(?:tab|tabs|tablet|cap|caps|capsule|inj|injection|syp|syr|syrup|susp|suspension|"
    r"oint|ointment|cream|gel|drops|neb|nebulisation|nebulization|inhaler|sachet|supp|suppository)"
    r"(?![A-Za-z])\.?[ \t]+", re.IGNORECASE)
_DRUG_WORD = _compile(r"[A-Za-z][A-Za-z\-+]*(?![A-Za-z0-9])[ \t]*")
_DOSING = _compile(
    r"(?:\d+(?:\.\d+)?[ \t]?)?(?:mg/m2|mg/kg|mg|mcg|µg|μg|ug|gm|g|ml|iu|units?|puffs?)(?![A-Za-z])"
    r"|[0-4½](?:\.5)?(?:-[0-4½](?:\.5)?){2,3}(?![\d\-])"
    r"|(?:od|bd|bid|tds|tid|qid|qds|hs|sos|stat|iv|im|sc|po)(?![A-Za-z])", re.IGNORECASE)
_NEXT_TOKEN = _compile(r"[^ \t\n]+[ \t]*")
_MAX_DRUG_WORDS, _DOSING_REACH = 3, 4
_EPONYM = re.compile(
    r"(?<![A-Za-z])[A-Z][a-z]+(?:[\- ][A-Z][a-z]+)?(?:['’]s)?[ \t]+"
    r"(?i:disease|syndrome|palsy|lymphoma|sarcoma|thyroiditis|contracture|chorea|an[ae]emia|"
    r"phenomenon|tumou?r|fracture|sign)(?![A-Za-z])")
_ASHA = _compile(
    r"(?<![A-Za-z])(?:ASHA(?=[ \t]+(?i:worker|workers|didi|karyakarta|sahyogini|sangini|bahu|facilitator))"
    r"|(?i:asha)(?=[ \t]+(?i:worker|workers|karyakarta|sahyogini|sangini))"
    r"|(?:(?<=\bby )|(?<=\bthe )|(?<=\ban )|(?<=\blocal ))ASHA(?![A-Za-z])|आशा(?=[ \t]+(?:कार्यकर्ता|वर्कर|सहयोगिनी|संगिनी)))")
_ABHA_LABEL = _compile(r"(?<![A-Za-z])ABHA(?![A-Za-z])")

_WORDS = re.compile(r"[A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+")
_ROLES = frozenset(unicodedata.normalize("NFKC", w) for w in ROLE_WORDS)
_DEPARTMENTS = frozenset(unicodedata.normalize("NFKC", w) for w in DEPARTMENT_WORDS)


def role_only(value: str) -> bool:
    """A hospital job and nothing else ("Lab Assistant", "ward boy", "स्टाफ")."""
    words = [w.casefold() for w in _WORDS.findall(value)]
    return any(w in _ROLES for w in words) \
        and all(w in _ROLES or w in _DEPARTMENTS for w in words)


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DOSE = _compile(
    r"(?<![A-Za-z0-9/])\d+(?:\.\d+)?[ \t]?(?:mg|mcg|µg|μg|ug|gm|g|ml|mL|IU|units?|puffs?|drops?)"
    r"(?![A-Za-z])")
_FREQUENCY = re.compile(r"(?<![\d\-])[0-4½](?:\.5)?(?:-[0-4½](?:\.5)?){2,3}(?![\d\-])")


# Detectors whose context states outright that a person is meant.
_EXPLICIT_PERSON = frozenset({"patient_label", "relative_label", "relation", "relation_subject",
                              "staff_role", "staff_done_by", "layout",
                              "care_of", "chat_sender", "report_of", "address"})


class Veto:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon

    def name_spans(self, text: str, tokens: Tokens | None = None) -> list[tuple[int, int]]:
        """Spans in which nothing may be treated as a patient or relative name."""
        return self._person_spans(text, tokens) + self._place_spans(text)

    def _place_spans(self, text: str) -> list[tuple[int, int]]:
        """Facility and landmark names ("Shanti Memorial Hospital")."""
        spans = [m.span() for m in _DEGREE_HOLDER.finditer(text)]
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
        for pattern in (_EPONYM, _ASHA, _ABHA_LABEL, _QUALIFICATION, _DEGREES):
            spans.extend(m.span() for m in pattern.finditer(text))
        return spans + self._drug_spans(text)

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
    def _drug_spans(text: str) -> list[tuple[int, int]]:
        """The medicine named between a dosage form and its dose."""
        spans = []
        for form in _DOSAGE_FORM.finditer(text):
            cursor, end = form.end(), None
            for count in range(_MAX_DRUG_WORDS + _DOSING_REACH):
                if count and _DOSING.match(text, cursor):
                    if end is not None:
                        spans.append((form.end(), end))
                    break
                word = _DRUG_WORD.match(text, cursor)
                if word and count < _MAX_DRUG_WORDS:
                    end = cursor + len(word.group(0).rstrip())
                    cursor = word.end()
                    continue
                other = _NEXT_TOKEN.match(text, cursor)
                if not other or end is None or count >= _DOSING_REACH:
                    break
                cursor = other.end()
        return spans

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
            if mention.etype in NAME_TYPES and role_only(mention.value):
                continue                    # a job, not a person: never a relative
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

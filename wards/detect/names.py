"""Names in free text, in English, Hindi and Hinglish.

Context rules come first (labels, relations, honorifics, chat senders), because they work
for names that appear in no list. The gazetteer then catches names without context.
"""

from __future__ import annotations

import re
import unicodedata

from wards.detect.tokens import MAX_NAME_TOKENS, Tokens
from wards.lexicon import HONORIFICS_DEVANAGARI, HONORIFICS_LATIN, KINSHIP, Lexicon
from wards.model import EntityType as E
from wards.model import Mention, Role


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(unicodedata.normalize("NFKC", pattern), flags)


_DEVA_HONORIFICS = frozenset(unicodedata.normalize("NFKC", w) for w in HONORIFICS_DEVANAGARI)

# What may sit between a trigger and the name it introduces.
_LEAD = re.compile(r"[ \t:\-–—.*_=]{0,6}")
_TITLE = _compile(
    r"(?:(?<![A-Za-z])(?:Mr|Mrs|Ms|Miss|Mstr|Master|Shri|Shree|Sri|Smt|Km|Kum|Kumari|Late|Sh)"
    r"(?![A-Za-z])\.?|(?:श्रीमती|श्री|सुश्री|कुमारी|स्वर्गीय)(?![\u0900-\u097f]))[ \t]*")

_SEP = r"[ \t]*(?:\([^)\n]{0,20}\))?[ \t]*[:=\-–][ \t]*"

_PATIENT_LABEL = _compile(
    r"(?:(?<![A-Za-z])(?P<word>patient(?:['’]s)?[ \t]*name|pt\.?[ \t]*name"
    r"|name[ \t]+of[ \t]+(?:the[ \t]+)?patient|patient|pt\.?|name|naam|mareez(?:[ \t]+ka[ \t]+naam)?)"
    r"|(?:मरीज़?|रोगी)(?:[ \t]*का[ \t]*नाम)?|नाम)" + _SEP, re.IGNORECASE)

_THING_BEFORE = re.compile(
    r"(?i)(?:hospital|lab|laboratory|drug|medicine|test|ward|file|doctor|dr\.?|brand|generic|"
    r"facility|scheme|bank|branch|village|district|father|mother|husband|guardian|relative|"
    r"attendant|test)(?:['’]s)?[ \t]*$")

_RELATIVE_LABEL = _compile(
    r"(?:(?<![A-Za-z])(?:father|mother|husband|wife|guardian|relative|attendant|spouse|informant|"
    r"nominee|witness|caregiver|next[ \t]+of[ \t]+kin|kin|pita|pati)(?:['’]s)?"
    r"(?:[ \t]*/[ \t]*(?:husband|father|pati))?(?:[ \t]+(?:name|ka[ \t]+naam))?"
    r"|(?:पति|पिता|माता|पत्नी|अभिभावक)(?:[ \t]*/[ \t]*(?:पति|पिता))?(?:[ \t]*का[ \t]*नाम)?)" + _SEP,
    re.IGNORECASE)

# S/o, D/o, W/o are always followed by a person.
_RELATION = _compile(
    r"(?:(?<![A-Za-z/])(?:[sdw][ \t]*/[ \t]*o|son[ \t]+of|daughter[ \t]+of|wife[ \t]+of|"
    r"husband[ \t]+of|spouse(?:[ \t]+of)?|child[ \t]+of|ward[ \t]+of|care[ \t]*giver)"
    r"(?![A-Za-z])|(?:सुपुत्री|सुपुत्र|पुत्री|पुत्र|धर्मपत्नी|पत्नी|आत्मजा|आत्मज)"
    r"(?![\u0900-\u097f]))\.?[ \t]*[:\-]?[ \t]*", re.IGNORECASE)

# "C/O" may mean "care of <person>" or "complains of <symptom>".
_CARE_OF = _compile(
    r"(?<![A-Za-z/])(?:(?P<care>c[ \t]*/[ \t]*o|care[ \t]+of)|(?P<history>h[ \t]*/[ \t]*o))"
    r"(?![A-Za-z])\.?[ \t]*[:\-]?[ \t]*", re.IGNORECASE)

_REPORT_OF = _compile(
    r"(?<![A-Za-z])(?:report|reports|results?|prescription|discharge|file|case)[ \t]+"
    r"(?:of|for)[ \t]+", re.IGNORECASE)

_ACTED_BY = _compile(
    r"(?<![A-Za-z])(?:called|witnessed|signed|informed|reported|collected|received|brought|"
    r"accompanied|raised|paid|escorted|identified|consented|submitted|dropped)[ \t]+"
    r"(?:in[ \t]+)?by[ \t]*[:\-]?[ \t]*", re.IGNORECASE)

_ROLE_AFTER = _compile(
    r"[ \t]*\([ \t]*(?:attendant|relative|visitor|caller|witness|friend|neighbou?r|brother|"
    r"sister|son|daughter|wife|husband|father|mother|guardian)(?![A-Za-z])", re.IGNORECASE)

_CHAT_SENDER = re.compile(
    r"(?m)^[ \t]*\[?\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4},?[ \t]+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?"
    r"[ \t]*(?:[AaPp]\.?[Mm]\.?)?[ \t]*(?:\]|[\-–])[ \t]*(?P<sender>[^:\n]{1,60}):")

_TIME_DASH = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?:[ \t]*(?:hrs|[AaPp][Mm]))?[ \t]*[\-–][ \t]*")

_POSTPOSITION = _compile(
    r"[ \t]+(?:(?:ka|ki|ke)[ \t]+(?:BP|bp|sugar|report|condition|haal|halat|bukhar|ilaj|dawa|"
    r"temperature|temp|pulse|test|operation|discharge)|ko[ \t]+(?:\d|bukhar|dawa|admit|discharge|"
    r"aaj|kal|raat|subah))(?![A-Za-z])"
    r"|[ \t]+को[ \t]+(?=\d|बुखार|दवा|भर्ती|खांसी|खाँसी|पेट|सिर|आज|कल)")

_POSSESSIVE = re.compile(
    r"(?i)['’]s[ \t]+(?:attendant|relative|son|daughter|wife|husband|father|mother|family|"
    r"condition|report|reports|bp|file|sample)(?![A-Za-z])")

# "Pt. Imli admitted ...": no colon, so the words after the name must read like a note.
_PATIENT_WORD = _compile(r"(?<![A-Za-z\u0900-\u097f])(?:Pt\.?|Patient|patient|मरीज़|मरीज|रोगी)[ \t]+")
_NOTE_CONTINUES = _compile(
    r"[ \t]*(?:\(|,[ \t]*(?:\d|aged?|[SDW]/o)|(?:was|is|has|had|came|presented|presents|admitted|"
    r"seen|reviewed|discharged|complains|complained|reports|c/o|aged|ka|ki|ko|ne)(?![A-Za-z])"
    r"|(?:को|ने|का|की)(?![\u0900-\u097f]))", re.IGNORECASE)
_SPOKEN_NAME = _compile(r"(?<![A-Za-z\u0900-\u097f])(?:naam|नाम)[ \t]+(?:hai[ \t]+|है[ \t]+)?(?=\S)")
_GIVEN_AFTER_COMMA = re.compile(r",[ \t]*")
_NAME_ENDS = re.compile(r"[ \t]*(?:[|;\n\r]|$)|[ \t]{2,}")

# "<name>      Age: ..." : forms put the name just left of the age column.
_BEFORE_AGE = _compile(
    r"(?:[ \t]{2,}|\t)(?=(?:आयु|उम्र|Age(?![A-Za-z])|AGE(?![A-Za-z])|Yrs|Sex(?![A-Za-z])))")

_PRE_PHONE_GAP = re.compile(
    r"(?i)^[ \t,;:\-–()]*(?:(?:mob(?:ile)?|ph(?:one)?|contact|no|number|call|on|at|मोबाइल|फोन)"
    r"\.?[ \t,;:\-–()]*){0,3}$")


def _mention(tokens: Tokens, run: list[int], role: Role, priority: int, source: str) -> Mention:
    start, end = tokens.span(run)
    etype = E.RELATIVE_NAME if role is Role.RELATIVE else E.PATIENT_NAME
    return Mention(etype, start, end, tokens.text[start:end], role=role, priority=priority,
                   source=source)


def _after(tokens: Tokens, position: int) -> list[int]:
    """The name that starts right after ``position`` (titles skipped)."""
    text = tokens.text
    position = _LEAD.match(text, position).end()
    title = _TITLE.match(text, position)
    if title:
        position = title.end()
    index = tokens.index_at_or_after(position)
    if index >= len(tokens) or tokens[index].start != position:
        return []
    return tokens.run_forward(index) or _medical_given_name(tokens, index)


def _medical_given_name(tokens: Tokens, index: int) -> list[int]:
    """"Patient Parkinson <surname>": after words that introduce a person, a capitalised
    medical word followed by a known name is the first part of that name."""
    word = tokens[index]
    if not (word.deva or word.text[0].isupper()) or not tokens.lexicon.is_medical(word.text) \
            or index + 1 >= len(tokens) or not tokens.joined(index, index + 1):
        return []
    rest = tokens.run_forward(index + 1, limit=MAX_NAME_TOKENS - 1)
    return [index, *rest] if rest and tokens.known(rest) else []


def _before(tokens: Tokens, position: int, gap: str = r"[ \t,]*") -> list[int]:
    """The name that ends right before ``position``."""
    index = tokens.index_ending_before(position)
    if index < 0 or not re.fullmatch(gap, tokens.text[tokens[index].end:position]):
        return []
    return tokens.run_backward(index)


class NameDetector:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon

    def detect(self, text: str, tokens: Tokens | None = None) -> list[Mention]:
        tokens = tokens or Tokens(text, self.lexicon)
        if not len(tokens):
            return []
        found: list[Mention] = []
        self._labels(tokens, found)
        self._relations(tokens, found)
        self._senders(tokens, found)
        self._phrases(tokens, found)
        self._honorifics(tokens, found)
        found.extend(self.gazetteer(text, tokens))
        return found

    # ------------------------------------------------------------ context rules

    def _labels(self, tokens: Tokens, found: list[Mention]) -> None:
        text = tokens.text
        for m in _RELATIVE_LABEL.finditer(text):
            run = _after(tokens, m.end())
            if run:
                found.append(_mention(tokens, run, Role.RELATIVE, 80, "relative_label"))
        for m in _PATIENT_LABEL.finditer(text):
            word = (m.group("word") or "").lower()
            if word in ("name", "naam") and _THING_BEFORE.search(text[:m.start()][-30:]):
                continue
            run = _after(tokens, m.end())
            if run:
                run = self._with_given_name(tokens, run)
                found.append(_mention(tokens, run, Role.PATIENT, 76, "patient_label"))
        for m in _PATIENT_WORD.finditer(text):
            run = _after(tokens, m.end())
            if run and _NOTE_CONTINUES.match(text, tokens.span(run)[1]):
                found.append(_mention(tokens, run, Role.PATIENT, 63, "patient_word"))
        for m in _SPOKEN_NAME.finditer(text):
            run = _after(tokens, m.end())
            if run:
                found.append(_mention(tokens, run, Role.PATIENT, 61, "spoken_name"))

    @staticmethod
    def _with_given_name(tokens: Tokens, run: list[int]) -> list[int]:
        """Extend "Surname, Given" when the given name ends the field."""
        text = tokens.text
        comma = _GIVEN_AFTER_COMMA.match(text, tokens.span(run)[1])
        if not comma or len(run) != 1:
            return run
        index = tokens.index_at_or_after(comma.end())
        if index >= len(tokens) or tokens[index].start != comma.end():
            return run
        given = tokens.run_forward(index)
        if given and _NAME_ENDS.match(text, tokens.span(given)[1]):
            return run + given
        return run

    def _relations(self, tokens: Tokens, found: list[Mention]) -> None:
        text = tokens.text
        for m in _RELATION.finditer(text):
            run = _after(tokens, m.end())
            if run:
                found.append(_mention(tokens, run, Role.RELATIVE, 82, "relation"))
                # "Imli, W/o Temjen": the person before the relation is the patient.
                subject = _before(tokens, m.start(), r"[ \t]*,[ \t]*")
                if subject:
                    found.append(_mention(tokens, subject, Role.PATIENT, 70, "relation_subject"))
        for m in _CARE_OF.finditer(text):
            run = _after(tokens, m.end())
            if not run:
                continue
            end = tokens.span(run)[1]
            looks_like_person = tokens.known(run) or (
                m.group("care") and len(run) >= 2
                and bool(re.match(r"[ \t]*(?:[,;(\n]|$|\d{6})", text[end:end + 12])))
            if looks_like_person:
                found.append(_mention(tokens, run, Role.RELATIVE, 80, "care_of"))

    def _senders(self, tokens: Tokens, found: list[Mention]) -> None:
        """A saved contact name ("Temjen Bhaiya") is removed whole, kinship word included."""
        for m in _CHAT_SENDER.finditer(tokens.text):
            index = tokens.index_at_or_after(m.start("sender"))
            inside = []
            while index < len(tokens) and tokens[index].end <= m.end("sender"):
                inside.append(index)
                index += 1
            named = [i for i in inside if tokens.is_candidate(i)
                     and tokens[i].lower not in HONORIFICS_LATIN
                     and tokens[i].text not in _DEVA_HONORIFICS]
            if named:
                last = named[-1]
                while last + 1 in inside and (tokens[last + 1].lower in HONORIFICS_LATIN
                                              or tokens[last + 1].text in _DEVA_HONORIFICS):
                    last += 1
                found.append(_mention(tokens, list(range(named[0], last + 1)), Role.RELATIVE,
                                      83, "chat_sender"))

    def _phrases(self, tokens: Tokens, found: list[Mention]) -> None:
        text = tokens.text
        for pattern, role, priority, source in (
                (_REPORT_OF, Role.PATIENT, 76, "report_of"),
                (_ACTED_BY, Role.RELATIVE, 66, "acted_by"),
                (_TITLE, Role.UNKNOWN, 62, "title")):
            for m in pattern.finditer(text):
                run = _after(tokens, m.end())
                if run:
                    found.append(_mention(tokens, run, role, priority, source))
        for m in _ROLE_AFTER.finditer(text):
            run = _before(tokens, m.start(), r"[ \t]*")
            if run:
                found.append(_mention(tokens, run, Role.RELATIVE, 72, "role_after"))
        for m in _POSTPOSITION.finditer(text):
            run = _before(tokens, m.start(), r"")
            if run:
                found.append(_mention(tokens, run, Role.PATIENT, 60, "postposition"))
        for m in _POSSESSIVE.finditer(text):
            run = _before(tokens, m.start(), r"")
            if run:
                found.append(_mention(tokens, run, Role.PATIENT, 61, "possessive"))
        for m in _BEFORE_AGE.finditer(text):
            run = _before(tokens, m.start(), r"")
            if run:
                found.append(_mention(tokens, run, Role.PATIENT, 63, "before_age"))
        # "14:00 - Md. Temjen - K/C/O ..." in nursing notes
        for m in _TIME_DASH.finditer(text):
            run = _after(tokens, m.end())
            if run and re.match(r"[ \t]*[\-–:]", text[tokens.span(run)[1]:][:4]):
                found.append(_mention(tokens, run, Role.PATIENT, 70, "time_entry"))

    def _honorifics(self, tokens: Tokens, found: list[Mention]) -> None:
        text = tokens.text
        for index, token in enumerate(tokens.items):
            word = token.lower
            if word not in HONORIFICS_LATIN and token.text not in _DEVA_HONORIFICS:
                continue
            if index == 0 or text[tokens[index - 1].end:token.start] not in (" ", "  ", "-"):
                continue
            run = tokens.run_backward(index - 1, limit=3)
            if not run:
                continue
            sender = word in KINSHIP and text[token.end:token.end + 1] == ":"
            role = Role.RELATIVE if sender else Role.PATIENT
            found.append(_mention(tokens, run, role, 68, "honorific"))

    def before_phone(self, text: str, phones: list[Mention],
                     tokens: Tokens) -> list[Mention]:
        """"called by Imli Jamir 90000-00000": two or more words just before a number."""
        found = []
        for phone in phones:
            index = tokens.index_ending_before(phone.start)
            # skip the label words ("mob", "phone") between the name and the number
            while index >= 0 and tokens.lexicon.is_stopword(tokens[index].text):
                index -= 1
            if index < 0 or not _PRE_PHONE_GAP.match(text[tokens[index].end:phone.start]):
                continue
            run = tokens.run_backward(index)
            if len(run) >= 2:
                found.append(_mention(tokens, run, Role.UNKNOWN, 58, "before_phone"))
        return found

    # ------------------------------------------------------------ gazetteer

    def gazetteer(self, text: str, tokens: Tokens | None = None) -> list[Mention]:
        tokens = tokens or Tokens(text, self.lexicon)
        found = []
        index = 0
        while index < len(tokens):
            run = tokens.run_forward(index)
            if not run:
                index += 1
                continue
            start, end = tokens.span(run)
            if tokens.known(run) and not self.lexicon.district(text[start:end]):
                found.append(_mention(tokens, run, Role.UNKNOWN, 50, "gazetteer"))
            index = run[-1] + 1
        return found

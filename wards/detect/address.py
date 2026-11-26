"""Split Indian postal addresses into parts that are removed, generalised or kept.

District and state are kept (research value). House, locality, landmark, village, tehsil
and PIN are personal data. In an address field only what is known to be public is kept: a
part that is no district, state, country or PIN prefix is removed.
"""

from __future__ import annotations

import re
import unicodedata

from wards.columns import VILLAGE_ENDING
from wards.lexicon import Lexicon
from wards.model import EntityType as E
from wards.model import Mention, Role


def _compile(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(unicodedata.normalize("NFKC", pattern), flags)


_HOUSE = _compile(
    r"(?<![A-Za-z])(?:H\.?[ \t]*No\.?|H/No\.?|House(?:[ \t]*(?:No\.?|Number))?|"
    r"Makan(?:[ \t]*(?:No\.?|Number))?|Flat(?:[ \t]*No\.?)?|Plot(?:[ \t]*No\.?)?|"
    r"Door[ \t]*No\.?|Qtr\.?(?:[ \t]*No\.?)?|Quarter[ \t]*No\.?|Gher[ \t]*No\.?|"
    r"मकान(?:[ \t]*(?:नं\.?|न\.|संख्या|नंबर))?|घर(?:[ \t]*(?:नं\.?|संख्या|नंबर)))"
    r"[ \t]*[:#\-]?[ \t]*[A-Za-z]?[\-/]?\d+(?:[/\-][A-Za-z0-9]+)*[A-Za-z]?(?![A-Za-z0-9])")

_PLACE_WORD = r"(?:[A-Z][A-Za-z]*|[\u0900-\u0963\u0971-\u097f]+|\d+)"
_PLACE = rf"{_PLACE_WORD}(?:[ \t]{_PLACE_WORD}){{0,3}}"

_LANDMARK_START = _compile(
    r"^(?:near(?:by)?|nr\.|opp\.?|opposite|behind|beside|besides|next[ \t]+to|adjacent(?:[ \t]+to)?|"
    r"in[ \t]+front[ \t]+of|b/h|close[ \t]+to)(?![A-Za-z]).+")
_LANDMARK_HINDI = _compile(r".*(?:के[ \t]*(?:पास|पीछे|सामने|निकट|बगल)(?![\u0900-\u097f])).*")
_TEHSIL = _compile(r"^(?:Tehsil|Tahsil|Teh\.|Taluka?|Block|तहसील|ब्लॉक)[ \t]*[:\-]?[ \t]*(?P<v>.+)$")
_DISTRICT = _compile(r"^(?:Distt\.?|District|Dist\.?|Zilla|Zila|Jila|जिला|ज़िला|जनपद)"
                     r"[ \t]*[:\-]?[ \t]*(?P<v>.*)$")
_VILLAGE = _compile(
    r"^(?:Vill(?:age)?\.?(?:[ \t]*(?:&|and)[ \t]*(?:Post|P\.?O\.?))?|Gram(?![ \t]+Panchayat)|Gaon|"
    r"R[ \t]*/[ \t]*o|ग्राम(?![ \t]+पंचायत)|गांव|गाँव)(?![A-Za-z\u0900-\u097f])[ \t]*[:\-]?[ \t]*(?P<v>.+)$")
_POST = _compile(r"^(?:Post(?:[ \t]*Office)?|P\.[ \t]?O\.|P\.[ \t]?S\.|Thana|(?:PO|PS)(?=[ \t])|डाकघर|पोस्ट|थाना)"
                 r"[ \t]*[:\-]?[ \t]*(?P<v>.+)$")
_WARD = _compile(r"(?<![A-Za-z])(?:Ward|वार्ड)[ \t]*(?:No\.?|नं\.?)?[ \t]*[:\-#]?[ \t]*\d+(?![\d(])")
_LOCALITY = _compile(
    r"(?<![A-Za-z\u0900-\u097f])(?:Mohalla|Muhalla|Mohallah|Nagar|Tola|Basti|Colony|Gali|Lane|Road|"
    r"Street|Chowk|Bazaa?r|Market|Sector|Enclave|Vihar|Kunj|Marg|Para|Pura|Patti|Mandi|"
    r"मोहल्ला|मुहल्ला|टोला|बस्ती|कॉलोनी|गली|मार्ग|नगर|चौक)(?![A-Za-z\u0900-\u097f])")
_PIN = re.compile(r"(?<![\d\-/])[1-9]\d{2}[ ]?\d{3}(?![\d\-/])")
_RELATION_PREFIX = _compile(r"^(?:[CSDWH][ \t]*/[ \t]*O|care[ \t]+of)(?![A-Za-z])\.?[ \t]*[:\-]?[ \t]*")
_KEEP = _compile(r"^(?:Uttar[ \t]+Pradesh|U\.?P\.?|Yugaantar[ \t]+Pradesh|India|IN|Bharat|"
                 r"उत्तर[ \t]+प्रदेश|भारत|N/?A|NA|Nil|None|Same|-+)$")
_SPLIT = re.compile(r"[,;|।\n]")
_WORDS = re.compile(r"[A-Za-z]+|[\u0900-\u0963\u0971-\u097f]+")


def _key(words: str) -> str:
    return "".join(_WORDS.findall(words)).casefold()


# States and union territories (public knowledge), and their common short forms.
_STATES = frozenset(_key(unicodedata.normalize("NFKC", name)) for name in """
    Andhra Pradesh|Arunachal Pradesh|Assam|Bihar|Chhattisgarh|Goa|Gujarat|Haryana|
    Himachal Pradesh|Jharkhand|Karnataka|Kerala|Madhya Pradesh|Maharashtra|Manipur|
    Meghalaya|Mizoram|Nagaland|Odisha|Orissa|Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|
    Tripura|Uttar Pradesh|Uttarakhand|Uttaranchal|West Bengal|Delhi|New Delhi|NCT|
    Jammu and Kashmir|Jammu Kashmir|Ladakh|Chandigarh|Puducherry|Pondicherry|
    Andaman and Nicobar Islands|Lakshadweep|Dadra and Nagar Haveli|Daman and Diu|
    Yugaantar Pradesh|UP|MP|AP|HP|UK|WB|TN|J and K|India|Bharat|
    आंध्र प्रदेश|अरुणाचल प्रदेश|असम|बिहार|छत्तीसगढ़|गोवा|गुजरात|हरियाणा|हिमाचल प्रदेश|झारखंड|
    कर्नाटक|केरल|मध्य प्रदेश|महाराष्ट्र|मणिपुर|मेघालय|मिज़ोरम|नागालैंड|ओडिशा|पंजाब|राजस्थान|
    सिक्किम|तमिलनाडु|तेलंगाना|त्रिपुरा|उत्तर प्रदेश|उत्तराखंड|पश्चिम बंगाल|दिल्ली|नई दिल्ली|
    जम्मू और कश्मीर|लद्दाख|चंडीगढ़|पुडुचेरी|उ.प्र|म.प्र|यूपी|भारत
""".split("|"))
# Words that may stand beside a public part without making it private.
_PUBLIC_FILLER = frozenset(unicodedata.normalize("NFKC", w) for w in
    "district distt dist zila zilla jila state country city pin pincode code zip postal na "
    "nil none same as above ditto unknown not available known जिला ज़िला जनपद राज्य देश "
    "पिन कोड".split())
_PIN_PREFIX = re.compile(r"(?<![\w])[1-9]\d{2}[ ]?(?:[*Xx•]{3})(?![\w*])")
_TEHSIL_AFTER = _compile(r"^(?P<v>.+?)[ \t]+(?:Tehsil|Tahsil|Taluka?|तहसील)$")
# Words that name no particular place: a phrase made only of them is never searched for
# in other files.
_GENERAL_PLACE = frozenset(unicodedata.normalize("NFKC", w) for w in
    "main old new road colony lane street gali marg nagar sector block phase civil lines "
    "line model town city area near ward mohalla muhalla mohallah para pura purwa tola "
    "basti chowk bazar bazaar market enclave vihar kunj patti mandi station bus stand post "
    "office hospital school mill gate east west north south camp home same above village "
    "gaon gram residency apartment apartments society extension गली मार्ग नगर मोहल्ला "
    "मुहल्ला टोला बस्ती कॉलोनी चौक पुरवा".split())
_NOT_A_VILLAGE = _compile(r"(?<![A-Za-z])(?:Dr|Doctor|Hospital|Clinic|College)(?![A-Za-z])|"
                          r"डॉ|डॉक्टर|अस्पताल")
# ``data`` of a place that may be searched for in the other files of the dataset.
LAST_PLACE = "last_place"

_PLACE_PIN = _compile(
    r"(?P<place>[A-Za-z\u0900-\u097f]+(?:[ \t][A-Za-z\u0900-\u097f]+)?)[ \t]*[\-–,][ \t]*"
    r"(?P<v>[1-9]\d{2}[ ]?\d{3})(?![\d\-/.,]\d|\d)")
# "R/o Village X, PS Y, Distt. Z": parse everything up to the district.
_RESIDENT_OF = _compile(
    r"(?<![A-Za-z])R[ \t]*/[ \t]*[Oo]\.?[ \t]+(?P<body>(?:[^,;\n]{1,60},[ \t]*){1,6}?"
    r"(?:Distt?\.?|District|जिला|ज़िला)[ \t]*[:\-]?[ \t]*[A-Za-z\u0900-\u097f]+)")
# "<... मंदिर के पास>" is a landmark wherever it is written.
_LANDMARK_HINDI_FREE = _compile(
    r"(?:[\u0900-\u0963\u0971-\u097f]+[ \t]){0,3}(?:मंदिर|मस्जिद|गुरुद्वार[ेा]|भवन|स्कूल|"
    r"विद्यालय|टंकी|चौक|स्टेशन|बाज़ार|बाजार)[ \t]*(?:के|की)[ \t]*(?:पास|पीछे|सामने|निकट|बगल)"
    r"(?![\u0900-\u097f])")
# Two-word villages ("X Kalan", "X खुर्द") and a short labelled line ending in a village.
# What follows a house number in running text: comma parts that end in a locality word.
_NEXT_PART = _compile(
    r"[ \t]*[,;\-–]?[ \t]*(?P<v>(?-i:(?:[A-Z][A-Za-z]*|[\u0900-\u0963\u0971-\u097f]+|\d+)"
    r"(?:[ \t](?:[A-Z][A-Za-z]*|[\u0900-\u0963\u0971-\u097f]+|\d+)){0,5}))")
_BUILDING = _compile(r"(?<![A-Za-z])(?:Residency|Apartments?|Society|Heights|Complex|Extension|"
                     r"Layout|Puram|Gardens?)(?![A-Za-z])")
_VILLAGE_PAIR = _compile(
    r"(?<![A-Za-z\u0900-\u097f])(?-i:[A-Z][a-z]+|[\u0900-\u0963\u0971-\u097f]+)[ \t]"
    r"(?:Kalan|Khurd|Khera|Purwa|कलां|खुर्द|खेड़ा|पुरवा)(?![A-Za-z\u0900-\u097f])")
_VILLAGE_LINE = re.compile(r"(?m)^[ \t]*\S+[ \t]*[:\-]?[ \t]+(?P<v>\S+)[ \t]*$")
_CARE_OF_WARD = _compile(
    r"(?<![A-Za-z/])C[ \t]*/[ \t]*O[^\n]{0,60}?(?<![A-Za-z])(?P<v>Ward[ \t]*\d+)(?![\d(])")
_ADDRESS_LINE = _compile(
    r"(?:(?<![A-Za-z])(?:Permanent[ \t]+|Present[ \t]+|Residential[ \t]+)?(?:Address|Addr\.?|R/o)"
    r"(?![A-Za-z])|पता(?![\u0900-\u097f])|निवास(?:[ \t]+स्थान)?)(?:[ \t]*\([^)\n]*\))?[ \t]*[:\-–][ \t]*"
    r"(?P<body>[^\n]+)")
_PIN_LABEL = _compile(r"(?:(?<![A-Za-z])(?:PIN(?:[ \t]*Code)?|Pincode|Postal[ \t]*Code|ZIP)|पिन(?:[ \t]*कोड)?)"
                      r"[ \t]*[:\-]?[ \t]*(?P<v>[1-9]\d{2}[ ]?\d{3})(?!\d)")
_VILLAGE_LABEL = _compile(
    r"(?:(?<![A-Za-z])(?:Village|Vill\.|Gram|Gaon)(?![A-Za-z])|(?:ग्राम|गांव|गाँव)(?![\u0900-\u097f]))"
    r"(?![ \t]+(?:Panchayat|पंचायत))[ \t]*[:\-]?[ \t]*(?P<v>(?-i:" + _PLACE + r"))")
_TEHSIL_LABEL = _compile(r"(?:(?<![A-Za-z])(?:Tehsil|Tahsil|Taluka?)(?![A-Za-z])|तहसील)"
                         r"[ \t]*[:\-]?[ \t]*(?P<v>(?-i:" + _PLACE + r"))")
_MOHALLA = _compile(r"(?:(?<![A-Za-z])(?:Mohalla|Muhalla|Mohallah)(?![A-Za-z])|मोहल्ला|मुहल्ला)"
                    r"[ \t]*[:\-]?[ \t]*(?-i:" + _PLACE + r")")
_LANDMARK_FREE = _compile(
    r"(?<![A-Za-z])(?:near|opp\.?|opposite|behind|beside)[ \t]+(?-i:(?:[A-Z][A-Za-z]*[ \t]){0,4}"
    r"(?:Mandir|Masjid|Gurudwara|Gurdwara|Church|School|College|Chowk|Tanki|Bhawan|Bhavan|Station|"
    r"Market|Bazaa?r|Park|Hospital|Crossing|Stand|Office|Temple|Mosque))(?![A-Za-z])")


# Words that complete a village name although they also occur in ordinary addresses.
_PLACE_SUFFIXES = frozenset(unicodedata.normalize("NFKC", w).casefold() for w in (
    "khurd", "kalan", "nagar", "purwa", "khera", "ganj", "patti", "bazar", "tola",
    "खुर्द", "कलां", "नगर", "पुरवा", "खेड़ा", "गंज", "पट्टी", "बाज़ार"))


class AddressDetector:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon

    def parse(self, text: str, start: int = 0, end: int | None = None,
              prose: bool = False) -> list[Mention]:
        """Classify every comma separated part of ``text[start:end]`` as an address.

        ``prose`` marks an address inside running text, where a long part may be a sentence
        and is left to the other detectors."""
        end = len(text) if end is None else end
        found: list[Mention] = []
        position = start
        pieces = []
        for separator in _SPLIT.finditer(text, start, end):
            pieces.append((position, separator.start()))
            position = separator.end()
        pieces.append((position, end))
        for low, high in pieces:
            while low < high and text[low] in " \t.:-":
                low += 1
            while high > low and text[high - 1] in " \t.:-":
                high -= 1
            if low < high:
                self._component(text, low, high, found, prose)
        village = self.village_in(text[start:end], prose)
        if village:
            for mention in reversed(found):
                if mention.etype in (E.VILLAGE, E.LOCALITY) and village in mention.value:
                    mention.data = LAST_PLACE
                    break
        return found

    def village_in(self, cell: str, prose: bool = False) -> str | None:
        """The village of an address written in one cell: its last comma part that is no
        administrative unit, if that part names a particular place. In running text a
        public part must follow it, or the "part" may be the rest of a sentence."""
        parts = [p.strip(" \t.:-–") for p in _SPLIT.split(cell)]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            return None
        public = False
        for part in reversed(parts):
            public = public or bool(_PIN.search(part))
            part = _PIN.sub("", _PIN_PREFIX.sub("", part)).strip(" \t.:-–")
            if part and (self._public(part) or _DISTRICT.match(part)):
                public = True
                continue
            if not part or _TEHSIL.match(part) or _TEHSIL_AFTER.match(part) or _POST.match(part) \
                    or _LANDMARK_START.match(part) or _LANDMARK_HINDI.match(part) \
                    or _HOUSE.search(part) or _WARD.search(part) \
                    or _RELATION_PREFIX.match(part):
                continue
            labelled = _VILLAGE.match(part)
            if labelled:
                part = labelled.group("v")
            words = part.split()
            distinctive = any(
                len(w) >= 3 and w.casefold() not in _GENERAL_PLACE
                and not self.lexicon.is_stopword(w) for w in _WORDS.findall(part))
            written = all(not w.isascii() or w[0].isupper() for w in words)
            if distinctive and written and len(words) <= 4 and (public or not prose) \
                    and not _NOT_A_VILLAGE.search(part) \
                    and not any(ch.isdigit() for ch in part):
                return part
            return None
        return None

    def _public(self, part: str) -> bool:
        """Is every word of ``part`` a district, state, country or PIN prefix?"""
        part = _PIN_PREFIX.sub(" ", part)
        words = _WORDS.findall(part)
        if any(ch.isdigit() for ch in part):
            return False
        index = 0
        while index < len(words):
            for count in range(min(5, len(words) - index), 0, -1):
                phrase = " ".join(words[index:index + count])
                if self.lexicon.district(phrase) or _key(phrase) in _STATES \
                        or count == 1 and phrase.casefold() in _PUBLIC_FILLER:
                    index += count
                    break
            else:
                return False
        return True

    def _component(self, text: str, low: int, high: int, found: list[Mention],
                   prose: bool) -> None:
        part = text[low:high]

        def add(etype: E, start: int, stop: int, priority: int = 74,
                role: Role = Role.UNKNOWN) -> None:
            while stop > start and text[stop - 1] in " \t.:-–":
                stop -= 1
            if stop > start:
                found.append(Mention(etype, start, stop, text[start:stop], role=role,
                                     priority=priority, source="address"))

        def rest(position: int, stop: int) -> None:
            """Whatever else the part holds from ``position`` is classified too."""
            while position < stop and not text[position].isalnum():
                position += 1
            if any(c.isalpha() for c in text[position:stop]):
                self._component(text, position, stop, found, prose)

        relation = _RELATION_PREFIX.match(part)
        if relation:
            after = part[relation.end():]
            name = re.match(r"[^\d(\-]+", after)
            house = _HOUSE.search(after)
            if name:
                stop = low + relation.end() + (min(name.end(), house.start()) if house
                                               else name.end())
                add(E.RELATIVE_NAME, low + relation.end(), stop, 81, Role.RELATIVE)
            if house:
                add(E.HOUSE, low + relation.end() + house.start(),
                    low + relation.end() + house.end(), 79)
                rest(low + relation.end() + house.end(), high)
            return

        # A PIN may trail any part: "Distt. Rampur - 123456"
        pins = list(_PIN.finditer(part))
        for pin in pins:
            add(E.PIN, low + pin.start(), low + pin.end(), 79)
        if pins:
            if not prose:
                rest(low + pins[-1].end(), high)
            high = low + pins[-1].start()
            while high > low and text[high - 1] in " \t.:-–":
                high -= 1
            part = text[low:high]
            if not part:
                return

        houses = list(_HOUSE.finditer(part))
        wards = list(_WARD.finditer(part))
        if houses or wards:
            for m in houses:
                add(E.HOUSE, low + m.start(), low + m.end(), 79)
            for m in wards:
                add(E.LOCALITY, low + m.start(), low + m.end(), 78)
            rest(low + max(m.end() for m in houses + wards), high)
            return

        if _LANDMARK_START.match(part) or _LANDMARK_HINDI.match(part):
            add(E.LANDMARK, low, high)
            return
        district = _DISTRICT.match(part)
        if district:                                 # kept, with whatever else is public
            value = district.group("v")
            words = list(re.finditer(r"\S+", value))
            for count in range(len(words), 0, -1):
                if not prose and self.lexicon.district(value[:words[count - 1].end()]):
                    rest(low + district.start("v") + words[count - 1].end(), high)
                    break
            return
        for pattern, etype in ((_TEHSIL, E.TEHSIL), (_VILLAGE, E.VILLAGE), (_POST, E.LOCALITY),
                               (_TEHSIL_AFTER, E.TEHSIL)):
            m = pattern.match(part)
            if m:
                add(etype, low + m.start("v"), low + m.end("v"), 75)
                return
        if _KEEP.match(part) or self.lexicon.district(part) or self._public(part):
            return
        if _LOCALITY.search(part):
            add(E.LOCALITY, low, high)
            return
        if re.fullmatch(r"[A-Za-z]?[\-]?\d+(?:/\d+)*[A-Za-z]?", part):
            add(E.HOUSE, low, high)
            return
        if any(c.isalpha() for c in part):           # unknown place: remove to be safe
            if len(part.split()) <= 5:
                add(E.VILLAGE, low, high, 52)
            elif not prose:
                add(E.LOCALITY, low, high, 52)

    def _place_words(self, value: str) -> str:
        """The leading words of ``value`` that can belong to a place name."""
        end = 0
        for word in re.finditer(r"\S+", value):
            token = word.group(0)
            if not token.isdigit() and token.casefold() not in _PLACE_SUFFIXES \
                    and self.lexicon.is_stopword(token) and not self.lexicon.district(token):
                break
            end = word.end()
        return value[:end]

    def detect_free(self, text: str) -> list[Mention]:
        """Address fragments inside running text or labelled lines."""
        found: list[Mention] = []

        def add(etype: E, start: int, end: int, priority: int) -> None:
            if end > start:
                found.append(Mention(etype, start, end, text[start:end], priority=priority,
                                     source="address"))

        for pattern in (_ADDRESS_LINE, _RESIDENT_OF):
            for m in pattern.finditer(text):
                found.extend(self.parse(text, m.start("body"), m.end("body"), prose=True))
        for m in _HOUSE.finditer(text):
            add(E.HOUSE, m.start(), m.end(), 73)
            # "House 364, Ward 20": a ward right after a house number is an address ward.
            ward = _WARD.search(text, m.end(), m.end() + 20)
            if ward:
                add(E.LOCALITY, ward.start(), ward.end(), 72)
            position = ward.end() if ward else m.end()
            while following := _NEXT_PART.match(text, position):
                value = following.group("v")
                ends = [w.end() for p in (_LOCALITY, _BUILDING) for w in p.finditer(value)]
                if not ends:
                    break
                length = max(ends)
                if len(value[:length].split()) == 1:
                    # "Sector 62" is a place; "Road" before "Traffic Accident" is not
                    number = re.match(r"[ \t]*\d+(?![\d/])", value[length:])
                    if not number:
                        break
                    length += number.end()
                position = following.start("v") + length
                add(E.LOCALITY, following.start("v"), position, 72)
        for m in _MOHALLA.finditer(text):
            add(E.LOCALITY, m.start(), m.end(), 71)
        for m in _LANDMARK_FREE.finditer(text):
            add(E.LANDMARK, m.start(), m.end(), 71)
        for m in _PIN_LABEL.finditer(text):
            add(E.PIN, m.start("v"), m.end("v"), 77)
        for m in _PLACE_PIN.finditer(text):
            words = m.group("place").split()
            if self.lexicon.district(m.group("place")) or self.lexicon.district(words[-1]):
                add(E.PIN, m.start("v"), m.end("v"), 77)
        for m in _LANDMARK_HINDI_FREE.finditer(text):
            add(E.LANDMARK, m.start(), m.end(), 70)
        for m in _VILLAGE_PAIR.finditer(text):
            add(E.VILLAGE, m.start(), m.end(), 66)
        for m in _CARE_OF_WARD.finditer(text):
            add(E.LOCALITY, m.start("v"), m.end("v"), 72)
        for m in _VILLAGE_LINE.finditer(text):
            word = m.group("v")
            if len(word) >= 5 and VILLAGE_ENDING.search(word) \
                    and (not word.isascii() or word[0].isupper()) \
                    and not self.lexicon.district(word) and not self.lexicon.is_stopword(word):
                add(E.VILLAGE, m.start("v"), m.end("v"), 62)
        for pattern, etype in ((_VILLAGE_LABEL, E.VILLAGE), (_TEHSIL_LABEL, E.TEHSIL)):
            for m in pattern.finditer(text):
                value = self._place_words(m.group("v"))
                if not value or etype is E.VILLAGE and self.lexicon.district(value):
                    continue
                add(etype, m.start("v"), m.start("v") + len(value), 70)
        return found

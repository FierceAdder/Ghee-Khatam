"""Split Indian postal addresses into parts that are removed, generalised or kept.

District and state are kept (research value). House, locality, landmark, village, tehsil
and PIN are personal data.
"""

from __future__ import annotations

import re
import unicodedata

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

_PLACE_PIN = _compile(
    r"(?P<place>[A-Za-z\u0900-\u097f]+(?:[ \t][A-Za-z\u0900-\u097f]+)?)[ \t]*[\-–,][ \t]*"
    r"(?P<v>[1-9]\d{2}[ ]?\d{3})(?![\d\-/.,]\d|\d)")
# "R/o Village X, PS Y, Distt. Z": parse everything up to the district.
_RESIDENT_OF = _compile(
    r"(?<![A-Za-z])R[ \t]*/[ \t]*[Oo]\.?[ \t]+(?P<body>(?:[^,;\n]{1,60},[ \t]*){1,6}?"
    r"(?:Distt?\.?|District|जिला|ज़िला)[ \t]*[:\-]?[ \t]*[A-Za-z\u0900-\u097f]+)")
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

    def parse(self, text: str, start: int = 0, end: int | None = None) -> list[Mention]:
        """Classify every comma separated part of ``text[start:end]`` as an address."""
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
                self._component(text, low, high, found)
        return found

    def _component(self, text: str, low: int, high: int, found: list[Mention]) -> None:
        part = text[low:high]

        def add(etype: E, start: int, stop: int, priority: int = 74,
                role: Role = Role.UNKNOWN) -> None:
            while stop > start and text[stop - 1] in " \t.:-–":
                stop -= 1
            if stop > start:
                found.append(Mention(etype, start, stop, text[start:stop], role=role,
                                     priority=priority, source="address"))

        relation = _RELATION_PREFIX.match(part)
        if relation:
            rest = part[relation.end():]
            name = re.match(r"[^\d(\-]+", rest)
            house = _HOUSE.search(rest)
            if name:
                stop = low + relation.end() + (min(name.end(), house.start()) if house
                                               else name.end())
                add(E.RELATIVE_NAME, low + relation.end(), stop, 81, Role.RELATIVE)
            if house:
                add(E.HOUSE, low + relation.end() + house.start(),
                    low + relation.end() + house.end(), 79)
            return

        # A PIN may trail any part: "Distt. Rampur - 123456"
        pins = list(_PIN.finditer(part))
        for pin in pins:
            add(E.PIN, low + pin.start(), low + pin.end(), 79)
        if pins:
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
            rest_start = max(m.end() for m in houses + wards)
            if rest_start < len(part) and any(c.isalpha() for c in part[rest_start:]):
                offset = low + rest_start
                while offset < high and not text[offset].isalnum():
                    offset += 1
                self._component(text, offset, high, found)
            return

        if _LANDMARK_START.match(part) or _LANDMARK_HINDI.match(part):
            add(E.LANDMARK, low, high)
            return
        district = _DISTRICT.match(part)
        if district:
            return                                   # kept
        for pattern, etype in ((_TEHSIL, E.TEHSIL), (_VILLAGE, E.VILLAGE), (_POST, E.LOCALITY)):
            m = pattern.match(part)
            if m:
                add(etype, low + m.start("v"), low + m.end("v"), 75)
                return
        if _KEEP.match(part) or self.lexicon.district(part):
            return
        if _LOCALITY.search(part):
            add(E.LOCALITY, low, high)
            return
        if re.fullmatch(r"[A-Za-z]?[\-]?\d+(?:/\d+)*[A-Za-z]?", part):
            add(E.HOUSE, low, high)
            return
        words = part.split()
        if any(c.isalpha() for c in part) and len(words) <= 5:
            add(E.VILLAGE, low, high, 52)            # unknown place: remove to be safe

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
                found.extend(self.parse(text, m.start("body"), m.end("body")))
        for m in _HOUSE.finditer(text):
            add(E.HOUSE, m.start(), m.end(), 73)
            # "House 364, Ward 20": a ward right after a house number is an address ward.
            ward = _WARD.search(text, m.end(), m.end() + 20)
            if ward:
                add(E.LOCALITY, ward.start(), ward.end(), 72)
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
        for pattern, etype in ((_VILLAGE_LABEL, E.VILLAGE), (_TEHSIL_LABEL, E.TEHSIL)):
            for m in pattern.finditer(text):
                value = self._place_words(m.group("v"))
                if not value or etype is E.VILLAGE and self.lexicon.district(value):
                    continue
                add(etype, m.start("v"), m.start("v") + len(value), 70)
        return found

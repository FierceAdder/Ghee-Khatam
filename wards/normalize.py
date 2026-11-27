"""Cross-script name and identifier normalisation.

* :func:`transliterate` turns Devanagari into rough Latin (scripts change, meaning never).
* :func:`phonetic_key` folds the spelling variants common in Indian names so that
  Kamla/Kamala, Reena/Rina, Chauhan/Chouhan or पांडे/Pandey compare equal, while
  Kamal/Kamla (different people) stay apart.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j",
    "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
    "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n", "प": "p", "फ": "ph", "ब": "b",
    "भ": "bh", "म": "m", "य": "y", "र": "r", "ऱ": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
# Consonant + nukta (the view decomposes precomposed nukta letters into this form).
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}
_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e",
    "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e", "ऎ": "e", "ऒ": "o",
}
_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai",
    "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o",
}
_VIRAMA = "्"
_NUKTA_MARK = "़"
_NASALS = {"ं", "ँ"}
_VISARGA = "ः"

DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def is_devanagari(text: str) -> bool:
    return bool(DEVANAGARI.search(text))


def transliterate(word: str) -> str:
    """Devanagari to lower-case Latin, with word-final schwa deletion."""
    word = unicodedata.normalize("NFD", word)
    out: list[str] = []
    pending_schwa = False        # the last consonant still carries its inherent "a"
    last_consonant = ""
    for char in word:
        if char in _CONSONANTS:
            if pending_schwa:
                out.append("a")
            out.append(_CONSONANTS[char])
            last_consonant = char
            pending_schwa = True
        elif char == _NUKTA_MARK:
            if out and last_consonant in _NUKTA:
                out[-1] = _NUKTA[last_consonant]
        elif char in _MATRAS:
            out.append(_MATRAS[char])
            pending_schwa = False
        elif char == _VIRAMA:
            pending_schwa = False
        elif char in _VOWELS:
            if pending_schwa:
                out.append("a")
            out.append(_VOWELS[char])
            pending_schwa = False
        elif char in _NASALS:
            if pending_schwa:
                out.append("a")
            out.append("n")
            pending_schwa = False
        elif char == _VISARGA:
            out.append("h")
            pending_schwa = False
        elif char.isascii():
            pending_schwa = False
            out.append(char.lower())
        # anything else (danda, joiners, other marks) carries no sound
    # A final inherent vowel is silent in Hindi names: कमल is "kamal", not "kamala".
    return "".join(out)


_DIGRAPHS = (
    ("ngh", "nh"), ("ksh", "ks"), ("chh", "c"), ("ch", "c"), ("sh", "s"), ("ph", "f"),
    ("bh", "b"), ("dh", "d"), ("th", "t"), ("kh", "k"), ("gh", "g"), ("jh", "j"),
    ("ck", "k"), ("w", "v"), ("q", "k"), ("z", "j"), ("x", "ks"),
)
_TAIL = re.compile(r"[aeiouy]+h?$")


@lru_cache(maxsize=100_000)
def skeleton(latin: str) -> tuple[str, str]:
    """Split a Latin name into ``(consonant skeleton, final vowel class)``."""
    text = re.sub(r"[^a-z]", "", latin.lower())
    if not text:
        return "", ""
    for old, new in _DIGRAPHS:
        text = text.replace(old, new)
    tail_class = ""
    match = _TAIL.search(text)
    body = text
    if match and match.start() > 0:
        tail = match.group(0).rstrip("h")
        body = text[:match.start()]
        if "y" in tail or tail.endswith(("i", "e")):
            tail_class = "i"
        elif tail.endswith("a"):
            tail_class = "a"
        else:
            tail_class = "u"
    head = body[0]
    head = {"e": "i", "o": "u"}.get(head, head)
    consonants = re.sub(r"[aeiouy]", "", body[1:])
    folded = re.sub(r"(.)\1+", r"\1", head + consonants)
    return folded, tail_class


def phonetic_key(latin: str) -> str:
    head, tail = skeleton(latin)
    return f"{head}|{tail}" if head else ""


def clean_token(token: str) -> str:
    """Lower-case a name token and strip the punctuation that clings to it."""
    return token.strip(" .'’`-_,:;()[]").lower().replace("’", "").replace("'", "")


_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")
_NON_DIGIT = re.compile(r"\D")


def digits_only(value: str) -> str:
    return _NON_DIGIT.sub("", value)


# What may stand between the parts of one identifier, wherever it is written.
ID_SEPARATOR = r"[ \t/\\\-_.:#|()]"
# At most two of them between two parts, or one with a blank either side ("AB : 12").
ID_GAP = rf"(?:{ID_SEPARATOR}{{0,2}}|[ \t]{ID_SEPARATOR}[ \t])"


def id_key(value: str) -> str:
    """Canonical form of an identifier: upper-case letters and digits only."""
    return _NON_ALNUM.sub("", value).upper()


def phone_key(value: str) -> str:
    """The last ten digits identify an Indian number regardless of +91 or 0 prefixes."""
    digits = digits_only(value)
    return digits[-10:] if len(digits) >= 10 else digits

"""Word tokens and the rules for stringing them into a name."""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass

from wards.lexicon import Lexicon
from wards.normalize import is_devanagari

_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[ऀ-ॣॱ-ॿ]+")
# Between two words of one name: one space, a hyphen, or the dot of an initial.
_JOIN = re.compile(r"^(?: |-)$")
_DOT_JOIN = re.compile(r"^\. ?$")
_ABBREVIATIONS = frozenset({"md", "mohd", "mhd", "mohammed", "sk", "kr", "mo"})
MAX_NAME_TOKENS = 5


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str

    @property
    def lower(self) -> str:
        return self.text.lower()

    @property
    def deva(self) -> bool:
        return is_devanagari(self.text)


class Tokens:
    def __init__(self, text: str, lexicon: Lexicon) -> None:
        self.text = text
        self.lexicon = lexicon
        self.items: list[Token] = []
        for m in _TOKEN.finditer(text):
            word, end = m.group(0), m.end()
            if len(word) > 2 and word[-2:].lower() in ("'s", "’s"):
                word, end = word[:-2], end - 2      # possessive: "Imli's"
            self.items.append(Token(m.start(), end, word))
        self._starts = [t.start for t in self.items]

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Token:
        return self.items[index]

    def index_at_or_after(self, position: int) -> int:
        return bisect_left(self._starts, position)

    def index_ending_before(self, position: int) -> int:
        """Index of the last token that ends at or before ``position`` (or -1)."""
        index = bisect_left(self._starts, position) - 1
        while index >= 0 and self.items[index].end > position:
            index -= 1
        return index

    def is_candidate(self, index: int) -> bool:
        """Could this word be part of a person's name?"""
        token = self.items[index]
        if self.lexicon.is_stopword(token.text):
            return False
        if token.deva:
            return len(token.text) > 1
        if not token.text[0].isupper():
            return False
        if len(token.text) == 1:      # an initial counts only when written "R."
            return self.text[token.end:token.end + 1] == "."
        return True

    def joined(self, left: int, right: int) -> bool:
        a, b = self.items[left], self.items[right]
        gap = self.text[a.end:b.start]
        if _JOIN.match(gap):
            return True
        return bool(_DOT_JOIN.match(gap)) and (len(a.text) == 1 or a.lower in _ABBREVIATIONS)

    def run_forward(self, index: int, limit: int = MAX_NAME_TOKENS) -> list[int]:
        run: list[int] = []
        while index < len(self.items) and len(run) < limit and self.is_candidate(index):
            if run and not self.joined(run[-1], index):
                break
            run.append(index)
            index += 1
        return run

    def run_backward(self, index: int, limit: int = MAX_NAME_TOKENS) -> list[int]:
        run: list[int] = []
        while index >= 0 and len(run) < limit and self.is_candidate(index):
            if run and not self.joined(index, run[0]):
                break
            run.insert(0, index)
            index -= 1
        return run

    def span(self, run: list[int]) -> tuple[int, int]:
        return self.items[run[0]].start, self.items[run[-1]].end

    def known(self, run: list[int]) -> bool:
        return any(len(self.items[i].text) > 1
                   and self.lexicon.is_known_name(self.items[i].text) for i in run)

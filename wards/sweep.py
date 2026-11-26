"""The variant sweep: once a name is known, remove every spelling of it.

Linking tells which names belong to which patient. Every spelling, in either script, is
then searched for in that patient's records, full names everywhere, and name parts in
metadata, headers, keys and file names.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from wards.detect.tokens import Tokens
from wards.lexicon import Lexicon
from wards.link import name_tokens
from wards.model import NAME_TYPES, Channel, Mention, Role, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F

_SWEEP_PRIORITY = 56
_JOIN = re.compile(r"^(?:[ _\-]|\. ?)$")
_SKIP_FIELDS = frozenset({F.CODE, F.ORG, F.DOCTOR_NAME, F.DISTRICT, F.STATE, F.SEX,
                          F.ATTACHMENT})


@dataclass
class NameBag:
    """The name keys of one patient (or of one record that has no patient)."""

    patient: set[str] = field(default_factory=set)
    relative: set[str] = field(default_factory=set)
    prefixes: set[str] = field(default_factory=set)

    def add(self, mention: Mention, lexicon: Lexicon) -> None:
        target = self.relative if mention.role is Role.RELATIVE else self.patient
        for word in name_tokens(mention.value, lexicon):
            key = lexicon.name_key(word)
            if key:
                target.add(key)
                self.prefixes.add(lexicon.canonical(word)[:3])

    def role_of(self, key: str) -> Role | None:
        if key in self.patient:
            return Role.PATIENT
        if key in self.relative:
            return Role.RELATIVE
        return None


class NameSweeper:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon
        self.sequences: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        self.patient_keys: set[str] = set()
        self.patient_words: set[str] = set()

    def register(self, mention: Mention) -> None:
        """Remember a detected name for the dataset-wide search."""
        # a learned guess is redacted where it was found but never searched for elsewhere
        if mention.etype not in NAME_TYPES or mention.source in ("path", "known", "model"):
            return
        words = name_tokens(mention.value, self.lexicon)
        keys = tuple(k for k in (self.lexicon.name_key(w) for w in words) if k)
        # Gazetteer-only guesses are not reliable enough to drive a global search.
        if mention.priority <= 50:
            return
        if len(keys) >= 2:
            for ordered in (keys, keys[-1:] + keys[:-1]):     # also "Surname Given"
                self.sequences[ordered[0]].add(ordered)
        if mention.role is Role.PATIENT:
            distinctive = [w for w in words if not self.lexicon.is_generic(w) and len(w) > 2]
            self.patient_keys.update(self.lexicon.name_key(w) for w in distinctive)
            self.patient_words.update(self.lexicon.canonical(w) for w in distinctive)

    def sweep(self, segment: Segment, bag: NameBag | None) -> list[Mention]:
        if segment.field in _SKIP_FIELDS or segment.numeric:
            return []
        text = segment.view.text
        tokens = Tokens(text, self.lexicon)
        count = len(tokens)
        if not count:
            return []
        loose = segment.record is None or segment.field is F.PATH \
            or segment.channel in (Channel.HEADER, Channel.KEY, Channel.METADATA)
        keys = [self.lexicon.name_key(t.text) for t in tokens.items]
        roles: list[Role | None] = [None] * count

        for index in range(count):
            for sequence in self.sequences.get(keys[index], ()):
                end = index + len(sequence)
                if end <= count and tuple(keys[index:end]) == sequence and all(
                        _JOIN.match(text[tokens[i].end:tokens[i + 1].start])
                        for i in range(index, end - 1)) \
                        and (loose or all(self._written_as_name(tokens, i)
                                          for i in range(index, end))):
                    for i in range(index, end):
                        roles[i] = roles[i] or (bag.role_of(keys[i]) if bag else None) \
                            or Role.UNKNOWN

        if segment.field is not F.CLINICAL:
            for index, token in enumerate(tokens.items):
                if roles[index] or not keys[index] or not self._written_as_name(tokens, index):
                    continue
                role = bag.role_of(keys[index]) if bag else None
                if role and (self.lexicon.is_known_name(token.text)
                             or self.lexicon.canonical(token.text)[:3] in bag.prefixes):
                    roles[index] = role
                elif loose and keys[index] in self.patient_keys \
                        and (self.lexicon.canonical(token.text) in self.patient_words
                             or self.lexicon.is_known_name(token.text)):
                    roles[index] = Role.UNKNOWN
        return self._group(text, tokens, roles)

    def _written_as_name(self, tokens: Tokens, index: int) -> bool:
        token = tokens[index]
        if len(token.text) < (2 if token.deva else 3):
            return False
        return tokens.is_candidate(index)

    def _group(self, text: str, tokens: Tokens, roles: list[Role | None]) -> list[Mention]:
        found: list[Mention] = []
        index = 0
        while index < len(roles):
            if roles[index] is None:
                index += 1
                continue
            last = index
            while last + 1 < len(roles) and roles[last + 1] is not None \
                    and _JOIN.match(text[tokens[last].end:tokens[last + 1].start]):
                last += 1
            group = roles[index:last + 1]
            role = Role.PATIENT if Role.PATIENT in group else \
                Role.RELATIVE if Role.RELATIVE in group else Role.UNKNOWN
            start, end = tokens[index].start, tokens[last].end
            etype = E.RELATIVE_NAME if role is Role.RELATIVE else E.PATIENT_NAME
            found.append(Mention(etype, start, end, text[start:end], role=role,
                                 priority=_SWEEP_PRIORITY, source="sweep"))
            index = last + 1
        return found

"""A learned check for names that no rule found.

The rules find names through cues: labels, relation words, honorifics, file layouts. A name
that appears once, in free text, with none of those, is missed. This module scores every
word the rules did not already cover and adds a detection for runs of words that look like
a person's name. It only ever *adds*: its detections are redacted like any other, but they
are never used to link records, never searched for in other files, and doctors, diseases
and doses stay protected by the medical veto.

The clues describe the kind of evidence, never the word itself, so a model trained on our
data does not learn our names or labels:

* the word's shape (capitalised, upper case, Devanagari) and length;
* how much its spelling looks like a name (letter patterns learned from name lists);
* whether it is in the name, ordinary-word or medical lists, or names a district;
* what surrounds it: separators, titles, relation words, honorifics, staff roles, an age;
* dataset statistics: in how many files it appears, whether it appears in files about no
  patient, and whether the rules found it as part of a name somewhere else.

Three interchangeable backends score the words: an averaged perceptron (standard library),
a CRF (python-crfsuite) and IndicBERT (ONNX Runtime). ``config/policy.yaml`` chooses one.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from wards.lexicon import HONORIFICS_LATIN, KINSHIP, RESOURCE_DIR, Lexicon
from wards.model import NAME_TYPES, QUASI_TYPES, Document, Mention, Role, Segment
from wards.model import EntityType as E
from wards.normalize import is_devanagari, transliterate

MODEL_DIR = RESOURCE_DIR / "models"
BACKENDS = ("off", "perceptron", "crf", "indicbert")
SOURCE = "model"
PRIORITY = 52

_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[ऀ-ॣॱ-ॿ]+")
# A written token before words() splits it: "HbA1c" is one token, though two words.
_TOKEN = re.compile(r"(?:[^\W_]|[ऀ-ॣ०-ॿ])+(?:['’][A-Za-z]+)*")
_INNER_CAPITAL = re.compile(r"[a-z][A-Z]")      # HbA1c, eGFR, pH
_MAX_ACRONYM = 8                                # CBNAAT, TRUENAT
_TITLES = frozenset("mr mrs ms miss smt shri shree sri kumari late km master baby".split())
_DOCTOR = frozenset({"dr", "doctor", "prof"})
_RELATION_BEFORE = re.compile(r"(?i)(?:[sdwbmfh][ \t]*/[ \t]*o|son of|daughter of|wife of|"
                              r"husband of|spouse|ward of|child of|c/o|care of)[ \t.:\-]*$")
_STAFF = frozenset("nurse sister technician pharmacist anm asha staff counsellor attendant "
                   "receptionist".split())
_LABEL_WORDS = frozenset("name naam patient pt rogi mareez client person holder".split())
_AGE_AFTER = re.compile(r"^[ \t,(]*\d{1,3}[ \t]*(?:/|y|Y|yrs|Yrs|years|वर्ष|,)")


@dataclass(frozen=True)
class Word:
    start: int
    end: int
    text: str
    whole: str = ""                 # the written token this word was cut from


def words(text: str) -> list[Word]:
    out = []
    for token in _TOKEN.finditer(text):
        whole = token.group(0)
        for m in _WORD.finditer(text, token.start(), token.end()):
            word, end = m.group(0), m.end()
            if len(word) > 2 and word[-2:].lower() in ("'s", "’s"):
                word, end = word[:-2], end - 2     # possessive, as the rules read it
            out.append(Word(m.start(), end, word, whole))
    return out


def _key(word: str) -> str:
    return word.casefold()


# ------------------------------------------------------------- dataset statistics


@dataclass
class Stats:
    """What the whole dataset says about each word, computed after the rules have run."""

    files: Counter = field(default_factory=Counter)        # word -> files containing it
    patientless: set[str] = field(default_factory=set)      # seen in files about no one
    named: set[str] = field(default_factory=set)            # inside a rule-found name
    total: int = 1

    @classmethod
    def build(cls, docs: Iterable[Document]) -> "Stats":
        stats = cls()
        for doc in docs:
            stats.total += 1
            seen: set[str] = set()
            personal = False
            for segment in doc.segments:
                text = segment.view.text
                seen.update(_key(w.text) for w in words(text))
                for mention in segment.mentions:
                    if mention.etype not in QUASI_TYPES:
                        personal = True
                    if mention.etype in NAME_TYPES and mention.source != SOURCE \
                            and mention.priority > 50:
                        stats.named.update(_key(w.text) for w in words(mention.value))
            stats.files.update(seen)
            if not personal:
                stats.patientless |= seen
        return stats

    def spread(self, word: str) -> str:
        n = self.files.get(_key(word), 0)
        return "1" if n <= 1 else "2-3" if n <= 3 else "4-10" if n <= 10 else "many"


# ------------------------------------------------------------------ name-likeness


class Likeness:
    """How much a spelling looks like a name: letter 2- to 4-grams, names against words."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or {}

    @classmethod
    def load(cls, path: Path = MODEL_DIR / "name_likeness.json") -> "Likeness":
        if not path.is_file():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def grams(word: str) -> list[str]:
        latin = transliterate(word) if is_devanagari(word) else word
        padded = f"^{latin.casefold()}$"
        return [padded[i:i + n] for n in (2, 3, 4) for i in range(len(padded) - n + 1)]

    @classmethod
    def train(cls, names: Iterable[str], others: Iterable[str]) -> "Likeness":
        positive, negative = Counter(), Counter()
        for word in names:
            positive.update(set(cls.grams(word)))
        for word in others:
            negative.update(set(cls.grams(word)))
        p_total, n_total = sum(positive.values()) + 1, sum(negative.values()) + 1
        weights = {}
        for gram in set(positive) | set(negative):
            ratio = math.log((positive[gram] + 1) / p_total) - \
                math.log((negative[gram] + 1) / n_total)
            if abs(ratio) > 0.4:
                weights[gram] = round(ratio, 3)
        return cls(weights)

    def score(self, word: str) -> float:
        grams = self.grams(word)
        return sum(self.weights.get(g, 0.0) for g in grams) / max(len(grams), 1)

    def bucket(self, word: str) -> str:
        s = self.score(word)
        return "very_low" if s < -1 else "low" if s < -0.3 else "mid" if s < 0.3 \
            else "high" if s < 1 else "very_high"


# ----------------------------------------------------------------------- features


def _shape(word: str) -> str:
    if is_devanagari(word):
        return "deva"
    if word.isupper() and len(word) > 1:
        return "upper"
    if word[0].isupper():
        return "title"
    return "lower"


def _cue(word: str | None) -> str:
    if word is None:
        return "none"
    low = word.casefold()
    for name, group in (("title", _TITLES), ("doctor", _DOCTOR), ("staff", _STAFF),
                        ("label", _LABEL_WORDS), ("honorific", HONORIFICS_LATIN),
                        ("kin", KINSHIP)):
        if low in group:
            return name
    return "word"


def _gap(text: str) -> str:
    gap = text.strip(" \t")
    if not text:
        return "joined"
    if "\n" in text:
        return "newline"
    if not gap:
        return "space" if len(text) < 3 else "wide"
    return {":": "colon", "-": "dash", ",": "comma", "(": "paren", ".": "dot", "|": "bar",
            ")": "close"}.get(gap[-1], "other")


class Featurizer:
    def __init__(self, lexicon: Lexicon, likeness: Likeness) -> None:
        self.lexicon, self.likeness = lexicon, likeness

    def lexical(self, word: str) -> list[str]:
        lex = self.lexicon
        out = []
        if lex.is_first_name(word):
            out.append("list=first")
        if lex.is_surname(word):
            out.append("list=surname")
        if lex.is_medical(word):
            out.append("list=medical")
        elif lex.is_stopword(word):
            out.append("list=ordinary")
        if lex.district(word):
            out.append("list=district")
        return out

    def features(self, text: str, items: list[Word], i: int, covered: list[bool],
                 stats: Stats, hide_lists: bool = False) -> list[str]:
        w = items[i].text
        prev = items[i - 1] if i > 0 else None
        prev2 = items[i - 2] if i > 1 else None
        nxt = items[i + 1] if i + 1 < len(items) else None
        before = text[prev.end if prev else max(0, items[i].start - 12):items[i].start]
        after = text[items[i].end:(nxt.start if nxt else items[i].end + 12)]
        lexical = [] if hide_lists else self.lexical(w)
        f = ["bias", f"shape={_shape(w)}", f"len={min(len(w), 12) // 3}",
             f"like={self.likeness.bucket(w)}", f"spread={stats.spread(w)}",
             f"gap_before={_gap(before)}", f"gap_after={_gap(after)}",
             f"cue_prev={_cue(prev.text if prev else None)}",
             f"cue_prev2={_cue(prev2.text if prev2 else None)}",
             f"cue_next={_cue(nxt.text if nxt else None)}",
             f"shape_prev={_shape(prev.text) if prev else 'none'}",
             f"shape_next={_shape(nxt.text) if nxt else 'none'}",
             f"covered_prev={covered[i - 1] if prev else 'none'}",
             f"covered_next={covered[i + 1] if nxt else 'none'}",
             *lexical]
        f += [f"{x}&shape={_shape(w)}" for x in lexical[:1]]
        if _key(w) in stats.patientless:
            f.append("seen_without_patient")
        if _key(w) in stats.named:
            f.append("named_elsewhere")
        if _RELATION_BEFORE.search(text[max(0, items[i].start - 16):items[i].start]):
            f.append("relation_before")
        if _AGE_AFTER.match(text[items[i].end:items[i].end + 12]):
            f.append("age_after")
        if prev is None or "\n" in text[prev.end:items[i].start]:
            f.append("line_start")
        f.append(f"like&shape={self.likeness.bucket(w)}&{_shape(w)}")
        f.append(f"cue_prev&shape={_cue(prev.text if prev else None)}&{_shape(w)}")
        return f


# ----------------------------------------------------------------------- backends


class Perceptron:
    """Averaged perceptron over the clues above (standard library only)."""

    def __init__(self, weights: dict[str, float] | None = None, threshold: float = 0.0) -> None:
        self.weights = weights or {}
        self.threshold = threshold

    @classmethod
    def load(cls, path: Path) -> "Perceptron":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["weights"], data.get("threshold", 0.0))

    def score(self, features: list[str]) -> float:
        return sum(self.weights.get(f, 0.0) for f in features)

    def predict_line(self, feats: list[list[str]]) -> list[bool]:
        return [self.score(f) > self.threshold for f in feats]


class Crf:
    def __init__(self, path: Path) -> None:
        import pycrfsuite  # a declared dependency only when this backend is chosen

        self.tagger = pycrfsuite.Tagger()
        self.tagger.open(str(path))

    def predict_line(self, feats: list[list[str]]) -> list[bool]:
        return [label == "N" for label in self.tagger.tag(feats)]


class IndicBert:
    """Fine-tuned IndicBERT token tagger, run with ONNX Runtime on the text itself."""

    MAX_PIECES = 254

    def __init__(self, directory: Path, threshold: float = 0.5) -> None:
        import onnxruntime  # declared dependency only when this backend is chosen
        import sentencepiece

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2            # fixed, for identical results
        options.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(directory / "indicbert_names.onnx"),
                                                    options,
                                                    providers=["CPUExecutionProvider"])
        self.pieces = sentencepiece.SentencePieceProcessor(
            model_file=str(directory / "indicbert_spiece.model"))
        self.threshold = threshold
        self.cache: dict[tuple, list[bool]] = {}

    BATCH = 32

    def predict_many(self, jobs: list[tuple[str, list[tuple[int, int]]]]) -> list[list[bool]]:
        """Several lines at once: one model call per batch of similar-length lines. Lines
        repeat across files (templates), so each distinct line is scored once."""
        import numpy

        distinct = {}
        for line, spans in jobs:
            distinct.setdefault((line, tuple(spans)), None)
        todo = [key for key in distinct if key not in self.cache]
        aligned = [align(self.pieces, line, list(spans), self.MAX_PIECES) for line, spans in todo]
        order = sorted(range(len(todo)), key=lambda i: len(aligned[i][0]))
        results: list[list[bool]] = [[] for _ in todo]
        for start in range(0, len(order), self.BATCH):
            chunk = order[start:start + self.BATCH]
            width = max(len(aligned[i][0]) for i in chunk)
            ids = numpy.zeros((len(chunk), width), dtype=numpy.int64)
            for row, i in enumerate(chunk):
                ids[row, :len(aligned[i][0])] = aligned[i][0]
            logits = self.session.run(None, {"input_ids": ids})[0]
            for row, i in enumerate(chunk):
                out = []
                for position in aligned[i][1]:
                    if position < 0:
                        out.append(False)
                        continue
                    a, b = (float(x) for x in logits[row, position])
                    out.append(1 / (1 + math.exp(a - b)) > self.threshold)
                results[i] = out
        for key, result in zip(todo, results, strict=True):
            self.cache[key] = result
        return [self.cache[(line, tuple(spans))] for line, spans in jobs]


def align(pieces, line: str, spans: list[tuple[int, int]], limit: int = 254):
    """Token ids for ``line`` ([CLS] … [SEP]) and, for each word span, the position of the
    piece holding the word's first character (-1 if the line was cut before it)."""
    proto = pieces.encode(line, return_type="proto")
    # piece offsets are UTF-8 byte offsets: map them to character offsets
    char_at, position = {}, 0
    for index, char in enumerate(line):
        char_at[position] = index
        position += len(char.encode("utf-8"))
    char_at[position] = len(line)
    starts = [char_at.get(p.begin, len(line)) for p in proto.pieces][:limit]
    ids = [2] + [p.id for p in proto.pieces][:limit] + [3]
    first = []
    for start, _ in spans:
        holder = -1
        for k, s in enumerate(starts):
            if s <= start:
                holder = k
            else:
                break
        first.append(holder + 1 if holder >= 0 else -1)      # +1 for [CLS]
    return ids, first


# --------------------------------------------------------------------- the detector


class NameModel:
    def __init__(self, backend: str, lexicon: Lexicon, directory: Path = MODEL_DIR) -> None:
        self.backend = backend
        self.featurizer = Featurizer(lexicon, Likeness.load(directory / "name_likeness.json"))
        if backend == "perceptron":
            self.model = Perceptron.load(directory / "name_perceptron.json")
        elif backend == "crf":
            self.model = Crf(directory / "name_crf.crfsuite")
        elif backend == "indicbert":
            settings = directory / "indicbert_names.json"
            threshold = json.loads(settings.read_text(encoding="utf-8"))["threshold"] \
                if settings.is_file() else 0.5
            self.model = IndicBert(directory, threshold)
        else:
            raise ValueError("unknown_name_model")
        self.lexicon = lexicon

    def candidates(self, items: list[Word], covered: list[bool]) -> list[bool]:
        """Words worth asking about (see :func:`ask`)."""
        return [ask(self.lexicon, w.text, covered[i], w.whole) for i, w in enumerate(items)]

    def mentions(self, segment: Segment, stats: Stats) -> list[Mention]:
        return self.document([segment], stats).get(id(segment), [])

    def document(self, segments: list[Segment], stats: Stats) -> dict[int, list[Mention]]:
        """Detections for many segments at once (the encoder runs them in batches)."""
        jobs = []                 # (segment, text, items, asked)
        for segment in segments:
            text = segment.view.text
            for line_start, line in _lines(text):
                items = [replace(w, start=w.start + line_start, end=w.end + line_start)
                         for w in words(line)]
                if not items:
                    continue
                covered = [any(m.etype in NAME_TYPES and m.start < w.end and w.start < m.end
                               for m in segment.mentions) for w in items]
                asked = self.candidates(items, covered)
                if any(asked):
                    jobs.append((segment, text, items, asked, covered, line_start, line))
        if self.backend == "indicbert":
            answers = self.model.predict_many(
                [(line.rstrip("\r\n"), [(w.start - start, w.end - start) for w in items])
                 for _, _, items, _, _, start, line in jobs])
        else:
            answers = [self.model.predict_line(
                [self.featurizer.features(text, items, i, covered, stats)
                 for i in range(len(items))])
                for _, text, items, _, covered, _, _ in jobs]
        found: dict[int, list[Mention]] = {}
        for (segment, text, items, asked, _, _, _), yes in zip(jobs, answers, strict=True):
            found.setdefault(id(segment), []).extend(
                _runs(text, items, [a and y for a, y in zip(asked, yes, strict=True)]))
        return found


def ask(lexicon: Lexicon, word: str, covered: bool, whole: str = "") -> bool:
    """May the model be asked about this word? Not if the rules cover it, if it is an
    ordinary or medical word, or if it is written like a code, test or unit: digits or an
    inner capital anywhere in the written token (``HbA1c``, ``SpO2``, ``eGFR``), or a
    short all-capital abbreviation (``HTN``, ``CBNAAT``)."""
    token = whole or word
    if covered or len(word) < 2 or lexicon.is_stopword(word) or lexicon.is_medical(token):
        return False
    if any(ch.isdigit() for ch in token) or _INNER_CAPITAL.search(token):
        return False
    return not (word.isascii() and word.isupper() and len(word) <= _MAX_ACRONYM)


def _lines(text: str):
    position = 0
    for line in text.splitlines(keepends=True):
        yield position, line
        position += len(line)


def _runs(text: str, items: list[Word], marked: list[bool]) -> list[Mention]:
    found, i = [], 0
    while i < len(items):
        if not marked[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(items) and marked[j + 1] \
                and text[items[j].end:items[j + 1].start] in (" ", "-"):
            j += 1
        start, end = items[i].start, items[j].end
        found.append(Mention(E.PATIENT_NAME, start, end, text[start:end], role=Role.UNKNOWN,
                             priority=PRIORITY, source=SOURCE))
        i = j + 1
    return found

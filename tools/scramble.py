#!/usr/bin/env python3
"""Replace the label and cue vocabulary of the practice data with made-up words (development only).

    python3 tools/scramble.py --out /tmp/scrambled --seed 1 [--tier labels|cues]
    python3 main.py --input /tmp/scrambled/input --output /tmp/scrambled/release
    python3 ../Secret-Wards-Toolkit/check_dev.py --output /tmp/scrambled/release \\
        --truth /tmp/scrambled/truth/truth.json

Measures how much detection depends on wording it already knows. Every word used in a CSV
header, a JSON key or a text label ("Name:", "Pita ka Naam -") is replaced, everywhere it
occurs, by a made-up word of the same length, case and script. ``--tier cues`` also replaces
honorifics, kinship words and S/o-style relations.

Names, identifiers (any chunk holding a digit), medical words and the clinical text the
scorer checks are never changed,
so the answer key stays exact and only the vocabulary moves. FHIR files keep their standard
keys. Nobody tunes against random words, so this test does not wear out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from mutate import Mutator
from utility import ROOT, default_truth

from wards.lexicon import HONORIFICS_DEVANAGARI, HONORIFICS_LATIN, KINSHIP, Lexicon

_RUN = re.compile(r"[A-Za-z]+|[ऀ-ॣॱ-ॿ]+")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_LABEL = re.compile(
    r"(?m)(?:^|\|[ \t]*|[ \t]{2,})[ \t]*(?:[*\-•#>]+[ \t]*)?"
    r"(?P<label>[A-Za-zऀ-ॿ][A-Za-zऀ-ॿ .'/&()]{0,28}?)[ \t]*(?::|：|[ \t]-[ \t])")
# A chunk holding digits is an identifier, date or dose: its letters ("IPD-2026-…") stay.
_CHUNK = re.compile(r"[^\s,;|\"'()\[\]{}]+")
_RELATION = re.compile(r"(?<![A-Za-z/])(?P<a>[SDWsdw])([ \t]*/[ \t]*)(?P<b>[Oo])(?![A-Za-z])")
# Grammar, not vocabulary: changing these would make the text unreadable, not unfamiliar.
_FUNCTION_WORDS = frozenset("""
of the and in on at to by for or with from no not is was are has had per
ka ki ke ko se me hai aur
का की के को से में है और पर ने तक भी या
""".split())
# Conventions shared by every Indian hospital, and the medical veto's anchors.
_UNIVERSAL = frozenset("""
dr doctor mr mrs ms miss shri smt sri kumari late
md mbbs ms dm dnb mch
""".split()) | {"डॉ", "श्री", "श्रीमती"}
_LATIN_C, _LATIN_V = "bcdfghjklmnpqrstvwxz", "aeiou"
_DEVA_C = "कखगघचछजझटठडढतथदधनपफबभमयरलवशसह"
_DEVA_M = ("", "ा", "ि", "ी", "ु", "ू", "े", "ै", "ो", "ौ")
_FORMATS = ("csv", "tsv", "json", "text")


def pieces(run: str) -> list[str]:
    """``PatientID`` -> ``Patient``, ``ID``; Devanagari runs stay whole."""
    return _CAMEL.findall(run) if run.isascii() else [run]


class Scrambler:
    def __init__(self, truth: dict, source: Path, seed: int, tier: str) -> None:
        self.truth, self.source, self.seed, self.tier = truth, source, seed, tier
        self.lexicon = Lexicon.load()
        self.words: dict[str, str] = {}
        self.letters: dict[str, str] = {}
        self._build()

    # ------------------------------------------------------------ vocabulary

    def _texts(self):
        for rel, spec in sorted(self.truth["files"].items()):
            if spec.get("expect") == "sanitize" and spec["format"] in _FORMATS:
                raw = (self.source / rel).read_bytes()
                yield spec, raw.decode(spec.get("encoding") or "utf-8")

    def _protected(self) -> set[str]:
        keep = set()
        for spec in self.truth["files"].values():
            for unit in spec["units"].values():
                for entity in unit["entities"]:
                    keep.update(t.casefold() for t in entity.get("tokens") or [])
                    if entity["type"] in ("PATIENT_NAME", "RELATIVE_NAME", "CANARY"):
                        keep.update(w.casefold() for w in _RUN.findall(entity["surface"]))
                for probe in unit.get("preserve") or []:
                    keep.update(p.casefold() for run in _RUN.findall(probe["text"])
                                for p in pieces(run))
        return keep

    def _candidates(self) -> set[str]:
        found: set[str] = set()

        def add(text: str) -> None:
            for run in _RUN.findall(text):
                found.update(p.casefold() for p in pieces(run))

        for spec, text in self._texts():
            if spec["format"] in ("csv", "tsv"):
                add(text.splitlines()[0] if text else "")
            elif spec["format"] == "json":
                for key in re.findall(r'"((?:[^"\\]|\\.)*)"\s*:', text):
                    add(key)
            else:
                for m in _LABEL.finditer(text):
                    if len(m.group("label").split()) <= 4:
                        add(m.group("label"))
        if self.tier == "cues":
            found |= {w.casefold() for w in HONORIFICS_LATIN | KINSHIP}
            found |= set(HONORIFICS_DEVANAGARI)
            found |= {"son", "daughter", "wife", "husband", "father", "mother", "guardian",
                      "spouse", "पुत्र", "पुत्री", "पत्नी", "पति", "पिता", "माता"}
        return found

    def _usable(self, word: str, protected: set[str]) -> bool:
        if word in protected or word in _FUNCTION_WORDS or word in _UNIVERSAL:
            return False
        if len(word) < (2 if not word.isascii() else 3):
            return False
        return not (self.lexicon.is_medical(word) or self.lexicon.district(word))

    def _build(self) -> None:
        protected = self._protected()
        corpus = {p.casefold() for _, text in self._texts() for run in _RUN.findall(text)
                  for p in pieces(run)}
        taken = set(corpus)
        for word in sorted(w for w in self._candidates() if self._usable(w, protected)):
            fake = self._fake(word, taken)
            taken.add(fake)
            self.words[word] = fake
        if self.tier == "cues":
            for letter in "sdwo":
                self.letters[letter] = self._pick(_LATIN_C, f"rel:{letter}", set(self.letters.values()) | set("csdwoh"))

    def _pick(self, alphabet: str, salt: str, avoid: set[str]) -> str:
        digest = hashlib.sha256(f"{self.seed}:{salt}".encode()).digest()
        for byte in digest:
            choice = alphabet[byte % len(alphabet)]
            if choice not in avoid:
                return choice
        raise RuntimeError("no letter left")

    def _fake(self, word: str, taken: set[str]) -> str:
        for attempt in range(1000):
            digest = hashlib.sha256(f"{self.seed}:{attempt}:{word}".encode()).digest()
            if word.isascii():
                fake = "".join((_LATIN_C if i % 2 == 0 else _LATIN_V)[digest[i] % (20 if i % 2 == 0 else 5)]
                               for i in range(len(word)))
            else:
                fake, i = "", 0
                while len(fake) < len(word):
                    fake += _DEVA_C[digest[i % 32] % len(_DEVA_C)] + \
                        _DEVA_M[digest[(i + 1) % 32] % len(_DEVA_M)]
                    i += 2
            if fake not in taken and not self.lexicon.is_known_name(fake) \
                    and not self.lexicon.is_stopword(fake) and not self.lexicon.district(fake):
                return fake
        raise RuntimeError("could not make a new word")

    # --------------------------------------------------------------- rewrites

    def rewrite(self, text: str) -> str:
        def run(m: re.Match[str]) -> str:
            word = m.group(0)
            if not word.isascii():
                return self.words.get(word, word)
            return _CAMEL.sub(lambda p: _shape(p.group(0), self.words.get(p.group(0).casefold())),
                              word)

        text = _CHUNK.sub(lambda c: c.group(0) if any(ch.isdigit() for ch in c.group(0))
                          else _RUN.sub(run, c.group(0)), text)
        if self.letters:
            text = _RELATION.sub(lambda m: _shape(m.group("a"), self.letters[m.group("a").lower()])
                                 + m.group(2) + _shape(m.group("b"), self.letters["o"]), text)
        return text

    def pointer(self, loc: str) -> str:
        if not loc.startswith("/"):
            return loc
        return "/".join(part if part.isdigit() or part == "[]" else self.rewrite(part)
                        for part in loc.split("/"))

    def run(self, destination: Path) -> None:
        shutil.rmtree(destination, ignore_errors=True)
        files = {}
        for rel, spec in sorted(self.truth["files"].items()):
            raw = (self.source / rel).read_bytes()
            entry = json.loads(json.dumps(spec))
            if spec.get("expect") == "sanitize" and spec["format"] in _FORMATS:
                encoding = spec.get("encoding") or "utf-8"
                raw = self.rewrite(raw.decode(encoding)).encode(encoding)
                self._rewrite_entry(entry)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            entry["size"] = len(raw)
            target = destination / "input" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            files[rel] = entry
        (destination / "truth").mkdir(parents=True, exist_ok=True)
        (destination / "truth" / "truth.json").write_text(
            json.dumps(dict(self.truth, files=files), ensure_ascii=False, indent=1),
            encoding="utf-8")
        (destination / "truth" / "scrambled_words.json").write_text(
            json.dumps({"tier": self.tier, "words": len(self.words)}, indent=1),
            encoding="utf-8")

    def _rewrite_entry(self, entry: dict) -> None:
        units = {}
        for loc, unit in entry["units"].items():
            unit["loc"] = self.pointer(unit.get("loc", loc))
            for entity in unit["entities"]:
                entity["surface"] = self.rewrite(entity["surface"])
                if entity.get("match") == "text":
                    entity["key"] = entity["surface"].casefold()
            for probe in unit.get("preserve") or []:
                probe["text"] = self.rewrite(probe["text"])
            units[self.pointer(loc)] = unit
        entry["units"] = units
        entry["generalizable"] = [
            "col:" + self.rewrite(g[4:]) if g.startswith("col:") else self.pointer(g)
            for g in entry.get("generalizable") or []]


def _shape(model: str, fake: str | None) -> str:
    """``fake`` written in the case of ``model``."""
    if fake is None:
        return model
    if model.isupper() and len(model) > 1:
        return fake.upper()
    if model[:1].isupper():
        return fake[:1].upper() + fake[1:]
    return fake


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--toolkit")
    ap.add_argument("--tier", choices=("labels", "cues"), default="labels")
    ap.add_argument("--source", help="an altered copy (input/ and truth/) to scramble instead")
    args = ap.parse_args()
    toolkit_truth = default_truth(args.toolkit)
    truth_path = Path(args.source) / "truth" / "truth.json" if args.source else toolkit_truth
    destination = Path(args.out).resolve()
    if ROOT == destination or destination in ROOT.parents \
            or destination == toolkit_truth.parents[2].resolve() \
            or args.source and destination == Path(args.source).resolve():
        raise SystemExit("refusing to write over the repository, the toolkit or the source")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    scrambler = Scrambler(truth, truth_path.parent.parent / "input", args.seed, args.tier)
    scrambler.run(destination)
    missing, total = Mutator.unmatched(destination)
    print(f"scrambled {len(scrambler.words)} words ({args.tier}); answer-key surfaces not "
          f"found in their file: {missing} of {total}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Train the learned name check (development only).

    python3 tools/train_names.py --train DIR [DIR ...] --valid DIR [DIR ...] \\
        [--backends perceptron,crf] [--out resources/models] [--dump FOLDER]

Each DIR holds ``input/`` and ``truth/truth.json`` (generated training data, the practice
data, or an altered copy of it). The real pipeline runs on each one with the model off, so
the words, what the rules already covered and the dataset statistics are exactly what the
detector sees at run time. Words are labelled from the answer key; doctor lines and other
text that must be kept are never labelled as names. Only the training side of the
generator may be used here: test ingredients are for measurement.

``--dump`` writes the same examples as JSON Lines for the IndicBERT trainer.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

from utility import ROOT

from wards.lexicon import Lexicon
from wards.model import NAME_TYPES, Channel
from wards.model import FieldType as F
from wards.namemodel import (SOURCE, Featurizer, Likeness, Perceptron, Stats, Word, ask,
                             words)
from wards.pipeline import Pipeline
from wards.policy import load_policy
from wards.security import SafeLog, Secret
from wards.textview import TextView

NAME_KINDS = {"PATIENT_NAME", "RELATIVE_NAME", "STAFF_NAME", "CANARY"}


@dataclass
class Line:
    text: str               # the whole segment (features look across line ends)
    items: list[Word]
    covered: list[bool]
    labels: list[bool]
    stats: Stats
    span: tuple[int, int] = (0, 0)      # where the line sits in the segment


def collect(dataset: Path, lexicon: Lexicon) -> list[Line]:
    truth = json.loads((dataset / "truth" / "truth.json").read_text(encoding="utf-8"))
    captured = {}
    real = Pipeline._date_orders

    def capture(self, states):
        captured["states"] = [s for s in states if s.alive]
        return real(self, states)

    import tempfile
    with tempfile.TemporaryDirectory() as folder, \
            mock.patch.object(Pipeline, "_date_orders", capture):
        pipeline = Pipeline(load_policy(ROOT / "config" / "policy.yaml"),
                            Secret(b"training"), SafeLog(io.StringIO()), lexicon)
        pipeline.run(dataset / "input", Path(folder) / "out")
    states = captured["states"]
    stats = Stats.build(s.doc for s in states)
    lines = []
    for state in states:
        spec = truth["files"].get(state.item.rel)
        if spec is None:
            continue
        surfaces = {TextView.of(e["surface"]).text for u in spec["units"].values()
                    for e in u["entities"] if e["type"] in NAME_KINDS and e["surface"].strip()}
        keep = {TextView.of(p["text"]).text for u in spec["units"].values()
                for p in u.get("preserve") or []}
        for segment in state.doc.segments:
            if segment.field is not F.TEXT or segment.numeric \
                    or segment.channel not in (Channel.CONTENT, Channel.ENCODED):
                continue
            text = segment.view.text
            name_spans, keep_spans = _spans(text, surfaces), _spans(text, keep)
            for line_start, line in _split(text):
                items = [replace(w, start=w.start + line_start, end=w.end + line_start)
                         for w in words(line)]
                if not items:
                    continue
                covered = [any(m.etype in NAME_TYPES and m.source != SOURCE
                               and m.start < w.end and w.start < m.end
                               for m in segment.mentions) for w in items]
                labels = [_inside(w, name_spans) and not _inside(w, keep_spans) for w in items]
                lines.append(Line(text, items, covered, labels, stats,
                                  (line_start, line_start + len(line.rstrip("\r\n")))))
    return lines


def _split(text: str):
    position = 0
    for line in text.splitlines(keepends=True):
        yield position, line
        position += len(line)


def _spans(text: str, needles: set[str]) -> list[tuple[int, int]]:
    spans = []
    for needle in needles:
        start = text.find(needle)
        while start >= 0:
            spans.append((start, start + len(needle)))
            start = text.find(needle, start + 1)
    return spans


def _inside(word: Word, spans: list[tuple[int, int]]) -> bool:
    return any(a <= word.start and word.end <= b for a, b in spans)


# ------------------------------------------------------------------- training


def candidates(lexicon: Lexicon, line: Line, covered_too: bool = False) -> list[bool]:
    """Words the model is asked about. In training, words the rules found are included
    too: the model must learn names the rules would miss, and it never sees whether a rule
    fired on the word itself."""
    return [ask(lexicon, w.text, line.covered[i] and not covered_too, w.whole)
            for i, w in enumerate(line.items)]


def train_likeness(lexicon: Lexicon, lines: list[Line]) -> Likeness:
    names = set(lexicon.first_names) | set(lexicon.surnames)
    others = set(lexicon.stopwords) | set(lexicon.medical)
    for line in lines:
        for word, label in zip(line.items, line.labels, strict=True):
            (names if label else others).add(word.text)
    return Likeness.train(names, others)


def train_perceptron(examples, epochs: int = 12, seed: int = 7) -> Perceptron:
    rng = random.Random(seed)
    weights, totals, stamps, step = {}, {}, {}, 0
    positives = sum(y for _, y in examples)
    boost = max(1.0, (len(examples) - positives) / max(positives, 1) / 4)
    for _ in range(epochs):
        rng.shuffle(examples)
        for feats, label in examples:
            step += 1
            guess = sum(weights.get(f, 0.0) for f in feats) > 0
            if guess != label:
                delta = (boost if label else -1.0)
                for f in feats:
                    totals[f] = totals.get(f, 0.0) + (step - stamps.get(f, 0)) * weights.get(f, 0.0)
                    stamps[f] = step
                    weights[f] = weights.get(f, 0.0) + delta
    averaged = {}
    for f, w in weights.items():
        total = totals.get(f, 0.0) + (step - stamps.get(f, 0)) * w
        if abs(total / step) > 1e-4:
            averaged[f] = round(total / step, 5)
    return Perceptron(averaged)


def examples_of(featurizer: Featurizer, lexicon: Lexicon, lines: list[Line],
                hide: float, rng: random.Random, covered_too: bool = True):
    out = []
    for line in lines:
        asked = candidates(lexicon, line, covered_too)
        for i, wanted in enumerate(asked):
            if wanted:
                out.append((featurizer.features(line.text, line.items, i, line.covered,
                                                line.stats, hide_lists=rng.random() < hide),
                            line.labels[i]))
    return out


def score(predicted: list[bool], gold: list[bool]) -> dict:
    tp = sum(p and g for p, g in zip(predicted, gold, strict=True))
    fp = sum(p and not g for p, g in zip(predicted, gold, strict=True))
    fn = sum(g and not p for p, g in zip(predicted, gold, strict=True))
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    f2 = 5 * precision * recall / max(4 * precision + recall, 1e-9)
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f2": round(f2, 3),
            "names": tp + fn, "words": len(gold)}


def choose_threshold(model: Perceptron, valid) -> float:
    scores = [(model.score(f), y) for f, y in valid]
    best, best_f2 = 0.0, -1.0
    for threshold in sorted({round(s, 2) for s, _ in scores})[::max(1, len(scores) // 400)]:
        f2 = score([s > threshold for s, _ in scores], [y for _, y in scores])["f2"]
        if f2 > best_f2:
            best, best_f2 = threshold, f2
    return best


def train_crf(featurizer, lexicon, lines: list[Line], path: Path, hide: float, rng) -> None:
    import pycrfsuite

    trainer = pycrfsuite.Trainer(verbose=False)
    for line in lines:
        feats = [featurizer.features(line.text, line.items, i, line.covered, line.stats,
                                     hide_lists=rng.random() < hide)
                 for i in range(len(line.items))]
        trainer.append(feats, ["N" if y else "O" for y in line.labels])
    trainer.set_params({"c1": 0.1, "c2": 0.05, "max_iterations": 150,
                        "feature.possible_transitions": True})
    trainer.train(str(path))


def evaluate_lines(predict, featurizer, lexicon, lines: list[Line],
                   covered_too: bool = False) -> dict:
    predicted, gold = [], []
    for line in lines:
        asked = candidates(lexicon, line, covered_too)
        if not any(asked):
            continue
        feats = [featurizer.features(line.text, line.items, i, line.covered, line.stats)
                 for i in range(len(line.items))]
        yes = predict(feats)
        for i, wanted in enumerate(asked):
            if wanted:
                predicted.append(yes[i])
                gold.append(line.labels[i])
    return score(predicted, gold)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--valid", nargs="+", required=True)
    ap.add_argument("--backends", default="perceptron,crf")
    ap.add_argument("--out", default=str(ROOT / "resources" / "models"))
    ap.add_argument("--dump", help="write examples as JSON Lines for the IndicBERT trainer")
    ap.add_argument("--hide-lists", type=float, default=0.5,
                    help="share of training examples that do not see the name lists")
    args = ap.parse_args()
    lexicon = Lexicon.load()
    rng = random.Random(11)
    train = [line for d in args.train for line in collect(Path(d), lexicon)]
    valid = [line for d in args.valid for line in collect(Path(d), lexicon)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    likeness = train_likeness(lexicon, train)
    (out / "name_likeness.json").write_text(json.dumps(likeness.weights, sort_keys=True),
                                            encoding="utf-8")
    featurizer = Featurizer(lexicon, likeness)
    print(f"training lines {len(train)}, validation lines {len(valid)}")
    backends = args.backends.split(",")
    if "perceptron" in backends:
        model = train_perceptron(examples_of(featurizer, lexicon, train, args.hide_lists, rng))
        model.threshold = choose_threshold(
            model, examples_of(featurizer, lexicon, valid, 0.0, rng, covered_too=True))
        (out / "name_perceptron.json").write_text(json.dumps(
            {"threshold": model.threshold, "weights": model.weights}, sort_keys=True),
            encoding="utf-8")
        for scope in (True, False):
            print("perceptron", "all words" if scope else "left by rules",
                  evaluate_lines(model.predict_line, featurizer, lexicon, valid, scope))
    if "crf" in backends:
        import pycrfsuite

        path = out / "name_crf.crfsuite"
        train_crf(featurizer, lexicon, train, path, args.hide_lists, rng)
        tagger = pycrfsuite.Tagger()
        tagger.open(str(path))
        for scope in (True, False):
            print("crf", "all words" if scope else "left by rules",
                  evaluate_lines(lambda f: [t == "N" for t in tagger.tag(f)], featurizer,
                                 lexicon, valid, scope))
    if args.dump:
        folder = Path(args.dump)
        folder.mkdir(parents=True, exist_ok=True)
        for name, lines in (("train", train), ("valid", valid)):
            with open(folder / f"{name}.jsonl", "w", encoding="utf-8") as handle:
                for line in lines:
                    a, b = line.span
                    handle.write(json.dumps({
                        "text": line.text[a:b],
                        "spans": [[w.start - a, w.end - a] for w in line.items],
                        "words": [w.text for w in line.items], "labels": line.labels,
                        "asked": candidates(lexicon, line, True),
                        "left": candidates(lexicon, line)}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fine-tune IndicBERT to tag name words, and export it for ONNX Runtime (development only).

    python3 tools/train_indicbert.py --model DIR --data DUMP --out FOLDER [--epochs 3]

``DIR`` is a local copy of ai4bharat/indic-bert (MIT licence, 33.4M parameters, gated on
Hugging Face: accept its terms and log in to download it). ``DUMP`` is the folder written by
``tools/train_names.py --dump``. Needs torch, transformers, onnx and onnxruntime, which the
firewall itself does not: at run time only the exported model, its sentencepiece vocabulary,
onnxruntime and sentencepiece are needed.

Writes ``indicbert_names.onnx`` (8-bit weights) and ``indicbert_spiece.model`` to FOLDER and
prints word-level precision and recall on the validation examples.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import sentencepiece
import torch
from torch import nn
from transformers import AlbertModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wards.namemodel import align  # noqa: E402

LIMIT = 254


class Tagger(nn.Module):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.encoder = AlbertModel.from_pretrained(str(directory))
        self.head = nn.Linear(self.encoder.config.hidden_size, 2)

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        mask = (input_ids != 0).long()
        hidden = self.encoder(input_ids=input_ids, attention_mask=mask).last_hidden_state
        return self.head(hidden)


def load(path: Path, pieces) -> list[tuple[list[int], list[int], list[bool], list[bool]]]:
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(raw)
        if not item["spans"]:
            continue
        ids, first = align(pieces, item["text"], [tuple(s) for s in item["spans"]], LIMIT)
        rows.append((ids, first, item["labels"], item.get("left") or item["asked"]))
    return rows


def batches(rows, size: int, rng: random.Random | None):
    order = list(range(len(rows)))
    if rng:
        rng.shuffle(order)
    for start in range(0, len(order), size):
        chunk = [rows[i] for i in order[start:start + size]]
        width = max(len(r[0]) for r in chunk)
        ids = torch.zeros((len(chunk), width), dtype=torch.long)
        target = torch.full((len(chunk), width), -100, dtype=torch.long)
        for row, (piece_ids, first, labels, asked) in enumerate(chunk):
            ids[row, :len(piece_ids)] = torch.tensor(piece_ids)
            for position, label, ask in zip(first, labels, asked, strict=True):
                if position >= 0 and ask:
                    target[row, position] = int(label)
        yield ids, target, chunk


def evaluate(model: Tagger, rows, scope: int) -> dict:
    """scope 3: every candidate word; scope 3 vs "left": only words the rules missed."""
    model.eval()
    tp = fp = fn = 0
    with torch.no_grad():
        for ids, _, chunk in batches(rows, 32, None):
            probs = torch.softmax(model(ids), -1)[..., 1]
            for row, (_, first, labels, asked) in enumerate(chunk):
                for position, label, ask in zip(first, labels, asked, strict=True):
                    if position < 0 or not ask:
                        continue
                    guess = bool(probs[row, position] > 0.5)
                    tp += guess and label
                    fp += guess and not label
                    fn += label and not guess
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"precision": round(precision, 3), "recall": round(recall, 3),
            "f2": round(5 * precision * recall / max(4 * precision + recall, 1e-9), 3),
            "names": tp + fn}


def export(model: Tagger, folder: Path, pieces_path: Path) -> Path:
    import onnx  # noqa: F401  (required by the exporter)
    from onnxruntime.quantization import QuantType, quantize_dynamic

    model.eval()
    plain = folder / "indicbert_names.fp32.onnx"
    torch.onnx.export(model, (torch.tensor([[2, 100, 200, 3]]),), str(plain),
                      input_names=["input_ids"], output_names=["logits"],
                      dynamic_axes={"input_ids": {0: "batch", 1: "length"},
                                    "logits": {0: "batch", 1: "length"}},
                      opset_version=17, dynamo=False)
    final = folder / "indicbert_names.onnx"
    quantize_dynamic(str(plain), str(final), weight_type=QuantType.QInt8)
    plain.unlink()
    shutil.copyfile(pieces_path, folder / "indicbert_spiece.model")
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    torch.manual_seed(5)
    torch.set_num_threads(args.threads)
    rng = random.Random(5)
    model_dir, data, out = Path(args.model), Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pieces_path = model_dir / "spiece.model"
    pieces = sentencepiece.SentencePieceProcessor(model_file=str(pieces_path))
    train, valid = load(data / "train.jsonl", pieces), load(data / "valid.jsonl", pieces)
    # training sees every candidate word (as in train_names.py), validation reports both
    train = [(i, f, labels, asked_all) for (i, f, labels, _), asked_all in
             zip(train, [json.loads(x)["asked"] for x in
                         (data / "train.jsonl").read_text(encoding="utf-8").splitlines()
                         if json.loads(x)["spans"]], strict=True)]
    model = Tagger(model_dir)
    positives = sum(sum(lab and a for lab, a in zip(r[2], r[3], strict=True)) for r in train)
    candidates = sum(sum(r[3]) for r in train)
    weight = torch.tensor([1.0, max(1.0, (candidates - positives) / max(positives, 1) / 3)])
    loss_fn = nn.CrossEntropyLoss(weight=weight, ignore_index=-100)
    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(), "lr": 3e-5},
        {"params": model.head.parameters(), "lr": 1e-3}], weight_decay=0.01)
    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        for ids, target, _ in batches(train, 16, rng):
            optimizer.zero_grad()
            loss = loss_fn(model(ids).reshape(-1, 2), target.reshape(-1))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss)
        print(f"epoch {epoch + 1}: loss {total:.1f}", flush=True)
    valid_all = [(i, f, labels, [True] * len(labels)) for i, f, labels, _ in valid]
    print("indicbert all words", evaluate(model, valid_all, 3))
    print("indicbert left by rules", evaluate(model, valid, 3))
    torch.save(model.state_dict(), out / "indicbert_names.pt")     # for re-export only
    path = export(model, out, pieces_path)
    print(f"exported {path.name}: {path.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

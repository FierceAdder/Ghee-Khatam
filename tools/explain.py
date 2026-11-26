#!/usr/bin/env python3
"""Show what every stage of the firewall did to a small input (development only).

    python3 tools/explain.py                      # rebuild docs/PIPELINE_WALKTHROUGH.md

Runs the real pipeline on ``docs/examples/walkthrough/input`` (invented records) and fills
the placeholders ``<!-- explain:NAME -->`` in ``docs/examples/walkthrough/template.md``
with what each stage actually did: field types and how they were decided, every detection
and the detector that found it, what the medical veto protected, what was learned and
swept, how records were linked, and the released files.

The output shows input values, so it must only ever be used on invented data: it refuses
to write into the repository unless the input is under ``docs/examples/``.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import contextlib
import io
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from unittest import mock

from utility import ROOT

from wards import pipeline as pipeline_module
from wards.columns import classify_label
from wards.detect.engine import Detector
from wards.link import Linker, record_names
from wards.model import Channel, DocKind
from wards.pipeline import Pipeline
from wards.policy import load_policy
from wards.security import SafeLog, Secret
from wards.veto import Veto

EXAMPLE = ROOT / "docs" / "examples" / "walkthrough"
DEMO_SECRET = b"walkthrough-demo-secret"

# What each detector relies on. "known words" breaks when the wording is new.
EVIDENCE = {
    "pattern": "shape of the value", "date": "shape of the value", "age": "shape of the value",
    "embedded": "decoded payload", "known": "seen in another file",
    "sweep": "seen in another file", "clipped": "rest of an overlapping find",
    "rare": "held by one patient", "gazetteer": "name list",
    "label": "known words (label)", "patient_label": "known words (label)",
    "relative_label": "known words (label)", "spoken_name": "known words (naam hai)",
    "patient_word": "known words (Patient)", "relation": "known words (S/o, W/o…)",
    "relation_subject": "known words (S/o, W/o…)", "care_of": "known words (C/O)",
    "role_after": "known words (husband, son…)", "acted_by": "known words (called by…)",
    "honorific": "known words (ji, sahab…)", "title": "known words (Mr, Smt…)",
    "postposition": "known words (ko, ka…)", "possessive": "known words ('s report)",
    "report_of": "known words (report of)", "before_age": "layout (name before Age)",
    "time_entry": "layout (time - name -)", "before_phone": "layout (name before phone)",
    "chat_sender": "layout (chat sender)", "staff_role": "known words (nurse, technician…)",
    "staff_done_by": "known words (collected by…)",
    "layout": "form layout (label typed by its values)", "address": "known words (H.No., near…)",
    "path": "file name pieces",
}


_KINDS = {"hid": "hospital or insurance no.", "aadhaar": "Aadhaar", "abha": "ABHA",
          "phone": "phone", "pan": "PAN", "email": "e-mail"}


class Recorder:
    def __init__(self) -> None:
        self.detected: dict[int, list] = {}
        self.vetoed: dict[int, list] = defaultdict(list)
        self.current = None
        self.knowledge = None
        self.evidence: list = []
        self.assigned: dict = {}
        self.contexts: dict = {}
        self.plan = None
        self.states: list = []

    def patches(self) -> list:
        recorder = self
        detect_segment, apply = Detector.detect_segment, Veto.apply
        detect_document, link = Detector.detect_document, Linker.link
        own_link, plan, report = Pipeline._link, Pipeline._plan_conditions, Pipeline._report
        learn = pipeline_module.Knowledge.learn
        merge = Pipeline._merge

        def on_segment(self, segment):
            recorder.current = segment
            try:
                return detect_segment(self, segment)
            finally:
                recorder.current = None

        def on_merge(self, segment, extra):
            recorder.current = segment
            try:
                return merge(self, segment, extra)
            finally:
                recorder.current = None

        def on_veto(self, text, mentions, tokens=None):
            kept = apply(self, text, mentions, tokens)
            if recorder.current is not None:
                recorder.vetoed[id(recorder.current)] += [m for m in mentions if m not in kept]
            return kept

        def on_document(self, doc):
            detect_document(self, doc)
            for segment in doc.segments:
                recorder.detected[id(segment)] = list(segment.mentions)

        def on_learn(self, docs):
            learn(self, docs)
            recorder.knowledge = self

        def on_link(self, evidence):
            recorder.evidence, recorder.assigned = evidence, link(self, evidence)
            return recorder.assigned

        def on_own_link(self, *args):
            contexts, sweeper = own_link(self, *args)
            recorder.contexts = contexts
            return contexts, sweeper

        def on_plan(self, *args):
            recorder.plan = plan(self, *args)
            return recorder.plan

        def on_report(self, states, *args):
            recorder.states = states
            return report(self, states, *args)

        return [mock.patch.object(Detector, "detect_segment", on_segment),
                mock.patch.object(Veto, "apply", on_veto),
                mock.patch.object(Detector, "detect_document", on_document),
                mock.patch.object(pipeline_module.Knowledge, "learn", on_learn),
                mock.patch.object(Linker, "link", on_link),
                mock.patch.object(Pipeline, "_link", on_own_link),
                mock.patch.object(Pipeline, "_plan_conditions", on_plan),
                mock.patch.object(Pipeline, "_report", on_report),
                mock.patch.object(Pipeline, "_merge", on_merge)]


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|") or " "


def _table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _block(text: str, language: str = "text") -> str:
    return f"```{language}\n{text.rstrip()}\n```"


class Explainer:
    def __init__(self, source: Path, output: Path, recorder: Recorder, log: str) -> None:
        self.source, self.output, self.r, self.log = source, output, recorder, log

    def section(self, name: str) -> str:
        return getattr(self, f"_{name}")()

    def _inputs(self) -> str:
        parts = []
        for path in sorted(p for p in self.source.rglob("*") if p.is_file()):
            rel = path.relative_to(self.source).as_posix()
            text = path.read_bytes().decode("utf-8", errors="replace")
            parts.append(f"**`{rel}`**\n\n" + _block(text, _language(rel)))
        return "\n\n".join(parts)

    def _ingest(self) -> str:
        rows = []
        for state in self.r.states:
            item = state.item
            kind = item.kind.value if item.kind else "unknown"
            outcome = "quarantined: " + state.reason if state.reason else "cleaned"
            rows.append([f"`{item.rel}`", kind, item.encoding or "-", outcome])
        return _table(["File", "Read as", "Encoding", "Outcome"], rows)

    def _parse(self) -> str:
        rows = []
        for state in self._alive():
            doc = state.doc
            seen = set()
            for segment in doc.segments:
                if segment.channel in (Channel.HEADER, Channel.KEY) or not segment.label \
                        or segment.label in seen:
                    continue
                seen.add(segment.label)
                how = "header or key word" if classify_label(segment.label) else "values"
                rows.append([f"`{doc.rel}`", segment.label, segment.field.value, how])
            if doc.kind is DocKind.TEXT:
                rows.append([f"`{doc.rel}`", "(whole note)", "text", "free text"])
        return _table(["File", "Column or key", "Typed as", "Decided by"], rows)

    def _detect(self) -> str:
        parts = []
        for state in self._alive():
            rows, kept = [], []
            for segment in state.doc.segments:
                for m in self.r.detected.get(id(segment), []):
                    rows.append([m.value, m.etype.value, m.source or "pattern",
                                 EVIDENCE.get(m.source or "pattern", "field type")
                                 if m.source != "field" else _field_basis(segment)])
                kept += [m.value for m in self.r.vetoed.get(id(segment), [])]
            part = f"**`{state.doc.rel}`**\n\n" + _table(
                ["Found", "As", "Detector", "Relies on"], rows)
            if kept:
                part += "\n\nProtected by the medical veto: " + ", ".join(
                    f"`{v}`" for v in sorted(set(kept)))
            parts.append(part)
        return "\n\n".join(parts)

    def _learn(self) -> str:
        k = self.r.knowledge
        values = [[v, t.value] for v, t in sorted(k.id_values.items())] + \
            [[v, t.value] for v, t in sorted(k.digit_values.items())]
        swept = []
        for state in self._alive():
            for segment in state.doc.segments:
                first = {(m.start, m.end) for m in self.r.detected.get(id(segment), [])}
                swept += [[f"`{state.doc.rel}`", m.value, m.etype.value, m.source]
                          for m in segment.mentions
                          if m.source in ("known", "sweep") and (m.start, m.end) not in first]
        text = "Identifiers learned:\n\n" + _table(["Value", "Type"], values)
        text += "\n\nID shapes learned: " + ", ".join(f"`{s}`" for s in sorted(k.shapes))
        text += "\n\nVillages learned: " + ", ".join(f"`{v}`" for v in sorted(k.villages.values()))
        if swept:
            text += "\n\nFound again only because another file revealed them:\n\n" + \
                _table(["File", "Found", "As", "By"], swept)
        return text

    def _link(self) -> str:
        rows = []
        lexicon = self.r.knowledge.lexicon
        for index, item in enumerate(self.r.evidence):
            record, summary = item.record, item.summary
            names = sorted({m.value for _, m in record_names(record, lexicon)})
            strong = sorted({_KINDS.get(kind, kind) for kind, _ in summary.strong})
            rows.append([f"`{record.doc.rel}` {record.loc}", ", ".join(names),
                         ", ".join(strong) or "-", ", ".join(sorted(summary.sexes)) or "-",
                         self.r.assigned.get(index) or "null"])
        stats = {k: v for k, v in sorted(self.r_stats().items()) if v}
        return _table(["Record", "Names in it", "Strong identifiers", "Sex", "Pseudonym"],
                      rows) + "\n\nLinking decisions: " + ", ".join(
            f"{k} {v}" for k, v in stats.items())

    def r_stats(self):
        line = next(json.loads(x) for x in self.log.splitlines()
                    if json.loads(x)["event"] == "linking")
        return {k: v for k, v in line.items() if isinstance(v, int) and k not in ("seq",)}

    def _privacy(self) -> str:
        rows, seen = [], set()
        for _, context in sorted(self.r.contexts.items()):
            if context.pseudonym and context.pseudonym not in seen:
                seen.add(context.pseudonym)
                rows.append([context.pseudonym, f"{context.offset:+d} days"])
        privacy = next(json.loads(x) for x in self.log.splitlines()
                       if json.loads(x)["event"] == "privacy")
        return _table(["Patient", "Date shift"], rows) + (
            f"\n\nAge band width chosen for this release: {privacy['age_band_years']} years. "
            f"Diagnoses held by one patient and suppressed: {privacy['suppressed_codes']}. "
            f"Patients alone in their (age band, sex, district) group: "
            f"{privacy['unique_band_sex_district']} of {privacy['patients']}.")

    def _outputs(self) -> str:
        parts = []
        for path in sorted(p for p in (self.output / "sanitized").rglob("*") if p.is_file()):
            rel = path.relative_to(self.output / "sanitized").as_posix()
            parts.append(f"**`sanitized/{rel}`**\n\n" + _block(path.read_text(encoding="utf-8"),
                                                                 _language(rel)))
        return "\n\n".join(parts)

    def _renamed(self) -> str:
        rows = [[f"`{s.item.rel}`", f"`{s.output}`"] for s in self._alive()
                if s.output != s.item.rel]
        return _table(["Input file name", "Released as"], rows) if rows else "(none)"

    def _records(self) -> str:
        return _block((self.output / "records.jsonl").read_text(encoding="utf-8"), "json")

    def _report(self) -> str:
        report = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        brief = {k: report[k] for k in ("status", "counts", "entities")}
        return _block(json.dumps(brief, indent=2), "json")

    def _log(self) -> str:
        """Selected lines, without the timing fields that differ from run to run."""
        keep = []
        for raw in self.log.splitlines():
            line = json.loads(raw)
            if line["event"] in ("run_started", "linking", "privacy", "self_scan",
                                 "run_finished"):
                shown = {k: v for k, v in line.items()
                         if k not in ("t", "seconds", "peak_rss_mb", "mac", "seq")
                         and (v != 0 or line["event"] == "self_scan")}
                keep.append(json.dumps(shown, ensure_ascii=False))
        return _block("\n".join(keep), "json")

    def _alive(self):
        return [s for s in self.r.states if s.alive]


def _field_basis(segment) -> str:
    if classify_label(segment.label or ""):
        return "known words (header or key)"
    return "shape of the column's values"


def _language(rel: str) -> str:
    return {"json": "json", "csv": "csv"}.get(rel.rsplit(".", 1)[-1], "text")


def explain(source: Path, template: Path, destination: Path) -> str:
    recorder = Recorder()
    with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
        for patch in recorder.patches():
            stack.enter_context(patch)
        output = Path(folder) / "release"
        # the learned name check is left off so this page is the same with or without its
        # packages installed; it is described in the text
        policy = replace(load_policy(ROOT / "config" / "policy.yaml"), name_model="off")
        Pipeline(policy, Secret(DEMO_SECRET), SafeLog(io.StringIO())).run(source, output)
        log = (output / "logs" / "run.jsonl").read_text(encoding="utf-8")
        explainer = Explainer(source, output, recorder, log)
        text = template.read_text(encoding="utf-8")
        for name in ("inputs", "ingest", "parse", "detect", "learn", "link", "privacy",
                     "renamed", "outputs", "records", "report", "log"):
            text = text.replace(f"<!-- explain:{name} -->", explainer.section(name))
    destination.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", default=str(EXAMPLE / "input"))
    ap.add_argument("--template", default=str(EXAMPLE / "template.md"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "PIPELINE_WALKTHROUGH.md"))
    args = ap.parse_args()
    source, destination = Path(args.input).resolve(), Path(args.out).resolve()
    examples = (ROOT / "docs" / "examples").resolve()
    if ROOT in destination.parents and examples not in source.parents:
        raise SystemExit("refusing: only invented examples under docs/examples may be "
                         "explained into the repository")
    explain(source, Path(args.template), destination)
    print(f"wrote {destination.relative_to(ROOT) if ROOT in destination.parents else destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the stages in order, isolating failures to the file that caused them.

    ingest -> parse -> detect -> learn & sweep -> date order -> link -> redact -> write

A problem in one file quarantines that file only. A problem in a dataset-wide stage
(linking) stops the run with a non-zero exit code: a partial release must never look
complete.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from wards import dates, documents, ingest, write
from wards.detect import Detector, resolve
from wards.errors import ConfigError, QuarantineError
from wards.knowledge import Knowledge, id_shape
from wards.lexicon import Lexicon
from wards.link import Linker, record_names
from wards.model import Channel, Document, Segment
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.policy import Policy
from wards.privacy import Privacy
from wards.redact import PatientContext, Redactor
from wards.security import SafeLog, Secret
from wards.sweep import NameBag, NameSweeper

DEFAULT_REFERENCE_YEAR = 2025
_QUASI = frozenset({E.DATE, E.AGE})


@dataclass
class FileState:
    item: ingest.InputFile
    doc: Document | None = None
    reason: str = ""
    output: str = ""                 # relative output path inside sanitized/
    order: str = dates.DMY

    @property
    def alive(self) -> bool:
        return self.doc is not None and not self.reason

    def quarantine(self, reason: str) -> None:
        self.reason = reason
        self.doc = None
        self.item.text = ""


@dataclass
class RunResult:
    files: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    seconds: float = 0.0


class Pipeline:
    def __init__(self, policy: Policy, secret: Secret, log: SafeLog | None = None,
                 lexicon: Lexicon | None = None) -> None:
        self.policy = policy
        self.log = log or SafeLog()
        self.lexicon = lexicon or Lexicon.load()
        self.detector = Detector(self.lexicon)
        self.privacy = Privacy(policy, secret)
        self.linker = Linker(self.lexicon, secret, policy.phone_hub_groups)
        self.secret = secret

    # ------------------------------------------------------------------- driver

    def run(self, input_dir: Path, output_dir: Path) -> RunResult:
        started = time.monotonic()
        input_dir, output_dir = _check_paths(input_dir, output_dir)
        sanitized = output_dir / "sanitized"
        sanitized.mkdir(parents=True, exist_ok=True)

        states = [FileState(ingest.read_file(path, input_dir, self.policy))
                  for path in ingest.list_files(input_dir)]
        for state in states:
            state.reason = state.item.reason
        self.log.event("ingested", files=len(states))

        self._each(states, "parse", self._parse, needs_doc=False)
        self._each(states, "detect", lambda s: self.detector.detect_document(s.doc))
        self.log.event("detected", files=sum(s.alive for s in states))

        knowledge = Knowledge(self.lexicon)
        knowledge.learn([s.doc for s in states if s.alive])
        self._each(states, "sweep", lambda s: self._apply_knowledge(s, knowledge))

        reference_year = self._reference_year(states)
        self._date_orders(states)
        contexts, sweeper = self._link(states, knowledge, reference_year)
        self.log.event("linked", records=len(contexts),
                       patients=len({c.pseudonym for c in contexts.values() if c.pseudonym}))

        redactor = Redactor(self.lexicon, self.privacy, knowledge)
        self._each(states, "redact",
                   lambda s: self._redact(s, redactor, sweeper, contexts, reference_year))
        self._name_outputs(states, knowledge, sweeper)
        self._each(states, "write",
                   lambda s: write.write_text_file(sanitized / s.output, write.serialize(s.doc)))
        # a file that failed while being written must leave nothing behind
        for state in states:
            if not state.alive and state.output:
                (sanitized / state.output).unlink(missing_ok=True)

        result = self._report(states, redactor, time.monotonic() - started)
        write.write_records(output_dir / "records.jsonl", result.records)
        write.write_report(output_dir / "report.json", result.files, result.counts,
                           dict(self._entities), result.seconds, peak_rss_mb())
        self.log.event("done", **{k: result.counts[k] for k in
                                  ("files_total", "files_sanitized", "files_quarantined",
                                   "records_processed", "records_linked")},
                       seconds=round(result.seconds, 2))
        return result

    def _each(self, states: list[FileState], stage: str, action, needs_doc: bool = True) -> None:
        for state in states:
            if state.reason or (needs_doc and state.doc is None):
                continue
            try:
                action(state)
            except QuarantineError as error:
                state.quarantine(error.reason)
            except Exception as error:  # noqa: BLE001 - one bad file must not stop the run
                self.log.problem(f"{stage}_failed", state.item.sha256, error)
                state.quarantine("processing_error")

    # ------------------------------------------------------------------- stages

    def _parse(self, state: FileState) -> None:
        state.doc = documents.parse(state.item, self.lexicon)

    def _apply_knowledge(self, state: FileState, knowledge: Knowledge) -> None:
        for record in state.doc.records:
            personal = any(m.etype not in _QUASI for _, m in record.mentions())
            for segment in record.segments:
                self._merge(segment, knowledge.sweep(segment, personal))
        for segment in state.doc.file_segments():
            self._merge(segment, knowledge.sweep(segment, False))

    def _merge(self, segment: Segment, extra: list) -> None:
        if not extra or segment.field is F.ATTACHMENT:
            return
        text = segment.view.text
        extra = self.detector.veto.apply(text, extra)
        segment.mentions = resolve(segment.mentions + extra, text)

    def _reference_year(self, states: list[FileState]) -> int:
        """The most common year in the data stands in for "now" (no wall clock)."""
        years: Counter = Counter()
        for state in states:
            if not state.alive:
                continue
            for segment in state.doc.segments:
                for m in segment.mentions:
                    if m.etype is E.DATE and m.data is not None:
                        value = m.data.resolve(dates.DMY) or m.data.resolve(dates.MDY)
                        if m.data.year is not None and value:
                            years[value.year] += 1
        if not years:
            return DEFAULT_REFERENCE_YEAR
        return max(sorted(years), key=lambda y: (years[y], y))

    def _date_orders(self, states: list[FileState]) -> None:
        """Day-first or month-first, with sibling files voting for ambiguous ones."""
        gathered = []
        votes: dict[str, Counter] = defaultdict(Counter)
        for state in states:
            if not state.alive:
                continue
            matches, keys, hint = [], set(), None
            for segment in state.doc.segments:
                hint = hint or dates.order_hint(segment.view.text)
                for m in segment.mentions:
                    if m.data is not None and m.etype in (E.DATE, E.DOB):
                        matches.append(m.data)
                    elif m.etype is E.MRN:
                        shape = id_shape(m.value)
                        if shape:
                            keys.add(shape)
                text = segment.view.text
                if segment.field is F.TEXT or segment.field is F.ORG:
                    head = text.lstrip()[:80].split("\n", 1)[0]
                    for start, end in self.detector.veto.name_spans(head):
                        phrase = head[start:end].casefold()
                        if "hospital" in phrase or "sadan" in phrase or "अस्पताल" in phrase:
                            keys.add(phrase)
            own = hint or dates.own_order(matches)
            if own:
                for key in sorted(keys):
                    votes[key][own] += 1
            gathered.append((state, matches, keys, hint))
        for state, matches, keys, hint in gathered:
            group: Counter = Counter()
            for key in sorted(keys):
                group.update(votes.get(key, {}))
            state.order = dates.choose_order(matches, hint, group)

    def _link(self, states: list[FileState], knowledge: Knowledge,
              reference_year: int) -> tuple[dict[str, PatientContext], NameSweeper]:
        evidence, orders = [], []
        for state in states:
            if not state.alive:
                continue
            for record in state.doc.records:
                evidence.append(self.linker.evidence(record, state.order, reference_year))
                orders.append(state.order)
        assigned = self.linker.link(evidence)

        sweeper = NameSweeper(self.lexicon)
        bags: dict[str, NameBag] = {}
        oldest: dict[str, int] = defaultdict(int)
        contexts: dict[str, PatientContext] = {}
        births: dict[str, set] = defaultdict(set)
        for index, item in enumerate(evidence):
            births[assigned.get(index) or item.record.key].update(item.summary.dobs)
        for index, item in enumerate(evidence):
            record = item.record
            pseudonym = assigned.get(index)
            bag_key = pseudonym or record.key
            bag = bags.setdefault(bag_key, NameBag())
            ages = [reference_year - d.year for d in item.summary.dobs]
            for _, mention in record_names(record, self.lexicon):
                sweeper.register(mention)
                if not item.ambiguous:
                    bag.add(mention, self.lexicon)
            for _, mention in record.mentions():
                if mention.etype is E.AGE and mention.value.isdigit():
                    ages.append(int(mention.value))
            if ages:
                oldest[bag_key] = max(oldest[bag_key], max(ages))
            contexts[record.key] = PatientContext(
                pseudonym=pseudonym,
                offset=self.privacy.shift_days(pseudonym or record.key),
                order=orders[index], reference_year=reference_year, bag=bag,
                district=knowledge.record_district(record),
                birth_dates=frozenset(births[bag_key]),
                shift_dates=pseudonym is not None
                or any(m.etype is not E.DATE for _, m in record.mentions()))
        for record_key, context in contexts.items():
            context.top_coded = oldest[context.pseudonym or record_key] \
                >= self.policy.age_top_code
        return contexts, sweeper

    def _redact(self, state: FileState, redactor: Redactor, sweeper: NameSweeper,
                contexts: dict[str, PatientContext], reference_year: int) -> None:
        for record in state.doc.records:
            context = contexts[record.key]
            for segment in record.segments:
                self._merge(segment, sweeper.sweep(segment, context.bag))
            redactor.record(record, context)
        # File-level values belong to no patient: dates stay, but a birth date has no
        # offset to hide behind, so it is always removed.
        loose = PatientContext(pseudonym=None, offset=0, order=state.order,
                               reference_year=reference_year, shift_dates=False,
                               top_coded=True)
        for segment in state.doc.file_segments():
            self._merge(segment, sweeper.sweep(segment, None))
            redactor.segment(segment, loose)

    # ---------------------------------------------------------------- file names

    def _name_outputs(self, states: list[FileState], knowledge: Knowledge,
                      sweeper: NameSweeper) -> None:
        """Rename any path component that contains personal data."""
        renamed: dict[str, str] = {}
        taken: set[str] = set()
        # unchanged paths are reserved first so a renamed file can never collide
        proposals = []
        for state in states:
            if not state.alive:
                continue
            parts = state.item.rel.split("/")
            new_parts = []
            for position, part in enumerate(parts):
                is_file = position == len(parts) - 1
                prefix = "/".join(parts[:position + 1])
                if prefix not in renamed:
                    renamed[prefix] = self._safe_component(part, is_file, prefix, state,
                                                           knowledge, sweeper)
                new_parts.append(renamed[prefix])
            proposals.append((state, "/".join(new_parts)))
        taken.update(path.casefold() for state, path in proposals if path == state.item.rel)
        for state, path in proposals:
            if path != state.item.rel:
                stem, dot, suffix = path.rpartition(".")
                candidate, number = path, 1
                while candidate.casefold() in taken:
                    number += 1
                    candidate = f"{stem}_{number}{dot}{suffix}" if dot else f"{path}_{number}"
                path = candidate
                taken.add(path.casefold())
            state.output = path

    def _safe_component(self, part: str, is_file: bool, prefix: str, state: FileState,
                        knowledge: Knowledge, sweeper: NameSweeper) -> str:
        stem, dot, suffix = part.rpartition(".") if is_file and "." in part[1:] \
            else (part, "", "")
        segment = Segment(stem, field=F.PATH, channel=Channel.METADATA)
        segment.mentions = self.detector.detect_segment(segment)
        self._merge(segment, knowledge.sweep(segment, False))
        self._merge(segment, sweeper.sweep(segment, None))
        if not any(m.etype not in _QUASI for m in segment.mentions):
            return part
        token = self.secret.mac("filename", f"{state.item.sha256}\x00{prefix}" if is_file
                                else prefix).hex()[:8]
        return f"{'file' if is_file else 'dir'}_{token}{dot}{suffix.lower()}"

    # ------------------------------------------------------------------- report

    def _report(self, states: list[FileState], redactor: Redactor,
                seconds: float) -> RunResult:
        files, records = [], []
        processed = linked = generalized = 0
        for state in states:
            entry = {"input_sha256": state.item.sha256}
            if not state.alive:
                entry.update(output=None, status="quarantined",
                             reason=state.reason or "processing_error")
                files.append(entry)
                continue
            output = f"sanitized/{state.output}"
            entry.update(output=output, status="sanitized")
            files.append(entry)
            for record in state.doc.records:
                processed += 1
                if not record.patient and not any(True for _ in record.mentions()):
                    continue            # nothing about a patient in this record
                linked += record.patient is not None
                generalized += record.generalized
                records.append({"file": output, "loc": record.loc, "patient": record.patient,
                                "status": "generalized" if record.generalized else "sanitized"})
        records.sort(key=lambda r: (r["file"], _natural(r["loc"])))
        sanitized = sum(f["status"] == "sanitized" for f in files)
        self._entities = redactor.counts
        counts = {
            "files_total": len(files),
            "files_sanitized": sanitized,
            "files_quarantined": len(files) - sanitized,
            "records_processed": processed,
            "identifiers_detected": sum(n for kind, n in redactor.counts.items()
                                        if kind not in (E.DATE.value, E.AGE.value)),
            "records_linked": linked,
            "records_generalized": generalized,
            "records_suppressed": 0,
            "records_quarantined": 0,
        }
        return RunResult(files=files, records=records, counts=counts, seconds=seconds)


def _natural(loc: str) -> list:
    return [(1, int(p), "") if p.isdigit() else (0, 0, p) for p in re.split(r"(\d+)", loc)]


def _check_paths(input_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    if not input_dir.is_dir():
        raise ConfigError("input_is_not_a_directory")
    source, target = input_dir.resolve(), output_dir.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ConfigError("input_and_output_overlap")
    if target.exists() and not target.is_dir():
        raise ConfigError("output_is_not_a_directory")
    stale = target / "sanitized"
    if (stale.is_dir() and any(stale.iterdir())) or (target / "report.json").exists():
        raise ConfigError("output_already_has_results")
    return source, target


def peak_rss_mb() -> float:
    try:
        import resource
    except ImportError:  # pragma: no cover - not available on Windows
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1048576 if sys.platform == "darwin" else usage / 1024, 1)

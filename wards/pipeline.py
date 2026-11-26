"""Run the stages in order, isolating failures to the file that caused them.

    ingest -> parse -> detect -> learn & sweep -> date order -> link -> redact
           -> self-scan & repair -> stage & verify -> publish

A problem in one file quarantines that file only. A problem in a dataset-wide stage
removes everything staged, writes a report marked "failed" and exits non-zero: a partial
release must never look complete.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from wards import crypto, dates, documents, ingest, logschema, validate, write
from wards.audit import Auditor
from wards.clinical import ConditionPlan
from wards.detect import Detector, resolve
from wards.errors import ConfigError, QuarantineError
from wards.knowledge import Knowledge, id_shape
from wards.layout import LayoutTypes
from wards.lexicon import Lexicon
from wards.namemodel import NameModel, Stats
from wards.link import Linker, record_names
from wards.model import Channel, DocKind, Document, Segment
from wards.model import QUASI_TYPES
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.policy import Policy
from wards.privacy import Privacy
from wards.redact import PatientContext, Redactor
from wards.release import QUARANTINE_NAME, Release
from wards.security import SafeLog, Secret, Terminated
from wards.sweep import NameBag, NameSweeper

DEFAULT_REFERENCE_YEAR = 2025
_RESERVE_BYTES = 16 << 20        # freed on failure so that clean-up still has memory
# The run limit is 15 minutes; the learned name check may use the first 8 of them.
MODEL_BUDGET_SECONDS = 8 * 60
_COUNT_NAMES = ("files_total", "files_sanitized", "files_quarantined", "records_processed",
                "identifiers_detected", "records_linked", "records_generalized",
                "records_suppressed", "records_quarantined")


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
    entities: dict = field(default_factory=dict)
    seconds: float = 0.0


class Pipeline:
    def __init__(self, policy: Policy, secret: Secret, log: SafeLog | None = None,
                 lexicon: Lexicon | None = None) -> None:
        self.policy = policy
        self.log = log or SafeLog()
        self.lexicon = lexicon or Lexicon.load()
        self.detector = Detector(self.lexicon)
        self.privacy = Privacy(policy, secret)
        self.linker = Linker(self.lexicon, secret, policy.phone_hub_groups,
                             fuzzy=policy.name_matching == "fuzzy")
        self.secret = secret
        self._demographics: list[tuple[str, str, str, int]] = []
        self.name_model = _load_name_model(policy.name_model, self.lexicon)
        self.model_skipped = 0
        self._repairs: dict[int, int] = {}
        self._unclaimed: set[str] = set()    # record keys grouped but not reported as linked
        self._stage_started = 0.0

    # ------------------------------------------------------------------- driver

    def run(self, input_dir: Path, output_dir: Path, sealed: bool = False,
            keep_quarantine: bool = False) -> RunResult:
        """``sealed``: the input folder was made by tools/seal.py. ``keep_quarantine``:
        keep an encrypted copy of every quarantined file in ``<output>/quarantine``."""
        started = time.monotonic()
        input_dir, output_dir = _check_paths(input_dir, output_dir)
        if keep_quarantine and _has_content(output_dir / QUARANTINE_NAME):
            raise ConfigError("output_already_has_results")
        # a wrong secret or a damaged archive is refused before anything is written
        source = crypto.Archive.open(input_dir, self.secret) if sealed else input_dir
        release = Release(output_dir, self.secret)
        self._stage_started = started
        states: list[FileState] = []
        reserve = bytearray(_RESERVE_BYTES)
        try:
            release.open()
            return self._run(source, release, states, started, keep_quarantine)
        except BaseException as error:
            del reserve
            release.abort()
            self._report_failure(output_dir, states, time.monotonic() - started, error)
            raise
        finally:
            release.close()              # no staged or half-written file on any way out

    def _run(self, source: Path | crypto.Archive, release: Release, states: list[FileState],
             started: float, keep_quarantine: bool) -> RunResult:
        for item in crypto.read_input(source, self.policy):
            states.append(FileState(item))
        for state in states:
            state.reason = state.item.reason
        self.log.record("run_started", files=len(states), policy=_digest(self.policy))
        if self.policy.name_model != "off" and self.name_model is None:
            self.log.record("problem", stage="model", reason="other", error="ImportError")
        self._stage_done("ingest", states)

        self._each(states, "parse", self._parse, needs_doc=False)
        self._stage_done("parse", states)
        # unknown form labels typed by their values across the whole dataset
        self.detector.layout = LayoutTypes.learn([s.doc for s in states if s.alive],
                                                 self.lexicon)
        self._each(states, "detect", lambda s: self.detector.detect_document(s.doc))
        self._stage_done("detect", states)

        knowledge = Knowledge(self.lexicon)
        knowledge.learn([s.doc for s in states if s.alive])
        auditor = Auditor(self.lexicon)
        self._each(states, "sweep", lambda s: self._apply_knowledge(s, knowledge, auditor))
        self._stage_done("sweep", states)
        if self.name_model is not None:
            stats = Stats.build(s.doc for s in states if s.alive)
            self._apply_model(states, stats, started + MODEL_BUDGET_SECONDS)
            self._stage_done("model", states)

        reference_year = self._reference_year(states)
        self._date_orders(states)
        contexts, sweeper = self._link(states, knowledge, reference_year)
        self.log.record("linking", **{k: self.linker.stats[k] for k in
                                      logschema.EVENTS["linking"][1]})
        self._stage_done("link", states)

        conditions = self._plan_conditions(states, contexts)
        self._generalise_ages(conditions)
        self._log_privacy(conditions)
        self._stage_done("conditions", states)
        redactor = Redactor(self.lexicon, self.privacy, knowledge)
        self._each(states, "redact", lambda s: self._redact(
            s, redactor, sweeper, conditions, contexts, reference_year, auditor))
        self._stage_done("redact", states)
        self._each(states, "audit",
                   lambda s: self._repairs.__setitem__(id(s), auditor.repair(s.doc)))
        self._stage_done("audit", states)
        self._name_outputs(states, knowledge, sweeper, auditor)
        self._stage_done("names", states)
        self._each(states, "stage", lambda s: self._stage(s, release, auditor))
        self._stage_done("stage", states)
        if keep_quarantine:
            kept = release.keep_quarantined(
                self.secret, source, [(s.item, s.reason) for s in states if not s.alive])
            self.log.record("quarantine_encrypted", files=kept)

        result = self._report(states, redactor, auditor, time.monotonic() - started)
        if self.name_model is not None:
            self.log.record("name_model", files_skipped=self.model_skipped)
        self.log.record("self_scan", repairs=sum(self._repairs.values()),
                        files_repaired=sum(n > 0 for n in self._repairs.values()),
                        files_quarantined=sum(s.reason == "self_scan_failed" for s in states),
                        records_withheld=result.counts["records_quarantined"])
        self._log_files(states)
        report = write.build_report(result.files, result.counts, result.entities,
                                    result.seconds, peak_rss_mb())
        self._finish("complete", result.counts, result.seconds)
        log = self.log.sealed(self.secret, _sha256(write.report_text(report)))
        if not auditor.is_clean(log):
            log = None                  # never publish a log that names anyone
            self.log.event("log_withheld", lines=1)
        release.publish(result.records, report, [s.output for s in states if s.alive], log)
        return result

    # ---------------------------------------------------------------------- log

    def _stage_done(self, stage: str, states: list[FileState]) -> None:
        now = time.monotonic()
        self.log.record("stage", stage=stage, seconds=round(now - (self._stage_started or now), 3),
                        peak_rss_mb=peak_rss_mb(), files=sum(s.alive for s in states))
        self._stage_started = now

    def _finish(self, status: str, counts: dict, seconds: float) -> None:
        self.log.record("run_finished", status=status, seconds=round(seconds, 2),
                        peak_rss_mb=peak_rss_mb(),
                        **{k: counts[k] for k in logschema.EVENTS["run_finished"][1]
                           if k in counts})

    def _generalise_ages(self, conditions: ConditionPlan) -> None:
        """Choose the release-wide band width (with l-diversity over diagnoses when asked)
        and, for Mondrian partitioning, each patient's age range."""
        held: dict[str, set[str]] = defaultdict(set)
        for code, patients in conditions.holders.items():
            for patient in patients:
                held[patient].add(code)
        self.privacy.choose_band_width([(district, sex, age, frozenset(held[patient]))
                                        for patient, district, sex, age in self._demographics])
        if self.policy.age_partitioning == "mondrian":
            self.privacy.partition(self._demographics)

    def _log_privacy(self, conditions: ConditionPlan) -> None:
        """How exposed patients still are, as counts (never the values)."""
        groups = Counter((district, sex, self.privacy.age_band(age, patient))
                         for patient, district, sex, age in self._demographics)
        shared_by_few = {p for code, patients in conditions.holders.items()
                         if code not in conditions.rare and len(patients) <= 3
                         for p in patients}
        self.log.record("privacy", age_band_years=self.privacy.band_width,
                        suppressed_codes=len(conditions.rare),
                        patients=len(self._demographics),
                        unique_band_sex_district=sum(n == 1 for n in groups.values()),
                        patients_with_code_held_by_3_or_fewer=len(shared_by_few))

    def _log_files(self, states: list[FileState]) -> None:
        """Per-file counts, by position in report.json (detail level only)."""
        if self.log.level != logschema.DETAIL:
            return
        for index, state in enumerate(states):
            reason = state.reason if state.reason in logschema.REASONS else "other"
            if not state.alive:
                self.log.record("file", file=index, status="quarantined", reason=reason)
                continue
            entities: Counter = Counter()
            sources: Counter = Counter()
            for segment in state.doc.segments:
                for mention in segment.mentions:
                    entities[mention.etype.value] += 1
                    sources[mention.source if mention.source in logschema.SOURCES
                            else "other"] += 1
            self.log.record("file", file=index, status="sanitized",
                            records=len(state.doc.records),
                            repairs=self._repairs.get(id(state), 0),
                            renamed=int(state.output != state.item.rel))
            self.log.record("file_entities", file=index, **entities)
            self.log.record("file_sources", file=index, **sources)

    def _report_failure(self, output_dir: Path, states: list[FileState],
                        seconds: float, error: BaseException) -> None:
        """Say plainly that nothing was released. Never hides the original error."""
        try:
            files = [{"input_sha256": s.item.sha256, "output": None, "status": "quarantined",
                      "reason": s.reason or "run_failed"} for s in states]
            counts = dict.fromkeys(_COUNT_NAMES, 0)
            counts.update(files_total=len(files), files_quarantined=len(files))
            self.log.record("problem", stage="run", reason=_failure_reason(error),
                            error=type(error).__name__)
            self._finish("failed", counts, seconds)
            report = write.build_report(files, counts, {}, seconds, peak_rss_mb(),
                                        status="failed")
            (output_dir / "sanitized").mkdir(parents=True, exist_ok=True)
            # the log first, so that the report stays the last thing written
            write.write_text_file(output_dir / "logs" / "run.jsonl", self.log.sealed(
                self.secret, _sha256(write.report_text(report))))
            write.write_report(output_dir / "report.json", report)
        except Exception as failure:  # noqa: BLE001 - report the class only
            self.log.problem("failure_report_not_written", error=failure)

    def _each(self, states: list[FileState], stage: str, action, needs_doc: bool = True) -> None:
        for index, state in enumerate(states):
            if state.reason or (needs_doc and state.doc is None):
                continue
            try:
                action(state)
            except QuarantineError as error:
                state.quarantine(error.reason)
            except Exception as error:  # noqa: BLE001 - one bad file must not stop the run
                self.log.record("problem", stage=stage, reason="processing_error",
                                error=type(error).__name__, file=index)
                state.quarantine("processing_error")

    # ------------------------------------------------------------------- stages

    def _parse(self, state: FileState) -> None:
        state.doc = documents.parse(state.item, self.lexicon)

    def _apply_knowledge(self, state: FileState, knowledge: Knowledge,
                         auditor: Auditor) -> None:
        for record in state.doc.records:
            personal = any(m.etype not in QUASI_TYPES for _, m in record.mentions())
            for segment in record.segments:
                self._merge(segment, knowledge.sweep(segment, personal))
        for segment in state.doc.file_segments():
            self._merge(segment, knowledge.sweep(segment, False))
        auditor.learn(state.doc)

    MODEL_CHUNK = 40                 # files per model call: batches stay full

    def _apply_model(self, states: list[FileState], stats: Stats, deadline: float) -> None:
        """Run the learned check over the dataset in chunks of files, so lines from many
        files share batches. It only adds detections: past its time budget, or if it
        fails, the remaining files are counted as skipped and keep the rules' result."""
        alive = [s for s in states if s.alive]
        for start in range(0, len(alive), self.MODEL_CHUNK):
            chunk = alive[start:start + self.MODEL_CHUNK]
            if time.monotonic() > deadline:
                self.model_skipped += len(chunk)
                continue
            segments = [s for state in chunk for s in state.doc.segments
                        if s.field is F.TEXT and not s.numeric
                        and s.channel in (Channel.CONTENT, Channel.ENCODED)]
            try:
                found = self.name_model.document(segments, stats)
            except Exception as error:  # noqa: BLE001 - an optional check must not cost files
                self.model_skipped += len(chunk)
                self.log.record("problem", stage="model", reason="other",
                                error=type(error).__name__)
                continue
            for segment in segments:
                self._merge(segment, found.get(id(segment), []))

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
        # Who a record is about, for names, ages and the date shift: the patient, else the
        # group it forms with other records of its file, else the record itself.
        local = {evidence[i].record.key: key for i, key in self.linker.local.items()}
        people = [assigned.get(i) or local.get(item.record.key) or item.record.key
                  for i, item in enumerate(evidence)]
        self._unclaimed = {evidence[i].record.key for i in self.linker.unclaimed}

        sweeper = NameSweeper(self.lexicon)
        bags: dict[str, NameBag] = {}
        oldest: dict[str, int] = defaultdict(int)
        contexts: dict[str, PatientContext] = {}
        births: dict[str, set] = defaultdict(set)
        for person, item in zip(people, evidence, strict=True):
            births[person].update(item.summary.dobs)
        for index, item in enumerate(evidence):
            record = item.record
            pseudonym = assigned.get(index)
            person = people[index]
            bag = bags.setdefault(person, NameBag())
            ages = [reference_year - d.year for d in item.summary.dobs]
            for _, mention in record_names(record, self.lexicon):
                sweeper.register(mention)
                if not item.ambiguous:
                    bag.add(mention, self.lexicon)
            for _, mention in record.mentions():
                if mention.etype is E.AGE and mention.value.isdigit():
                    ages.append(int(mention.value))
            if ages:
                oldest[person] = max(oldest[person], max(ages))
            contexts[record.key] = PatientContext(
                pseudonym=pseudonym,
                offset=self.privacy.shift_days(person),
                order=orders[index], reference_year=reference_year, bag=bag,
                district=knowledge.record_district(record),
                birth_dates=frozenset(births[person]),
                shift_dates=pseudonym is not None
                or any(m.etype is not E.DATE for _, m in record.mentions()))
        # a local group moves its dates together or not at all
        moved = {key for record_key, key in local.items()
                 if contexts[record_key].shift_dates}
        for person, item in zip(people, evidence, strict=True):
            context = contexts[item.record.key]
            context.shift_dates |= local.get(item.record.key) in moved
            context.top_coded = oldest[person] >= self.policy.age_top_code
        self._demographics = _demographics(evidence, assigned, contexts)
        return contexts, sweeper

    def _plan_conditions(self, states: list[FileState],
                         contexts: dict[str, PatientContext]) -> ConditionPlan:
        plan = ConditionPlan(self.policy.rare_diagnosis_patients)
        linked = []
        for state in states:
            if state.alive:
                plan.learn(state.doc)
                linked += [(contexts[r.key].pseudonym, r) for r in state.doc.records
                           if contexts[r.key].pseudonym]
        plan.decide(linked)
        return plan

    def _redact(self, state: FileState, redactor: Redactor, sweeper: NameSweeper,
                conditions: ConditionPlan, contexts: dict[str, PatientContext],
                reference_year: int, auditor: Auditor) -> None:
        for record in state.doc.records:
            context = contexts[record.key]
            personal = context.pseudonym is not None \
                or any(m.etype not in QUASI_TYPES for _, m in record.mentions())
            for segment in record.segments:
                self._merge(segment, sweeper.sweep(segment, context.bag))
                if personal:         # documents about nobody keep their medical content
                    self._merge(segment, conditions.mentions(segment))
            redactor.record(record, context)
            if record.key in self._unclaimed:
                record.patient = None     # written in the text, but not claimed as a link
        # File-level values belong to no patient: dates stay, but a birth date has no
        # offset to hide behind, so it is always removed.
        loose = PatientContext(pseudonym=None, offset=0, order=state.order,
                               reference_year=reference_year, shift_dates=False,
                               top_coded=True)
        for segment in state.doc.file_segments():
            self._merge(segment, sweeper.sweep(segment, None))
            redactor.segment(segment, loose)
        auditor.learn(state.doc)

    def _stage(self, state: FileState, release: Release, auditor: Auditor) -> None:
        """Write one file to the stage, read it back and refuse it if anything is wrong."""
        try:
            release.add(state.output, write.serialize(state.doc))     # ciphertext on disk
            written = self._reread(state.doc, release.read(state.output))
            faults = validate.problems(state.doc, written)
            if auditor.leaks(written):
                faults.insert(0, "self_scan_failed")     # the graver of the faults
        except BaseException:
            release.discard(state.output)
            raise
        if faults:
            release.discard(state.output)
            raise QuarantineError(faults[0])

    def _reread(self, source: Document, text: str) -> Document:
        kind = DocKind.JSON if source.kind is DocKind.FHIR else source.kind
        item = ingest.InputFile(rel=source.rel, sha256=source.sha256, text=text, kind=kind,
                                delimiter=source.options.get("delimiter", ","))
        return documents.parse(item, self.lexicon)

    # ---------------------------------------------------------------- file names

    def _name_outputs(self, states: list[FileState], knowledge: Knowledge,
                      sweeper: NameSweeper, auditor: Auditor) -> None:
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
                                                           knowledge, sweeper, auditor)
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
                        knowledge: Knowledge, sweeper: NameSweeper, auditor: Auditor) -> str:
        stem, dot, suffix = part.rpartition(".") if is_file and "." in part[1:] \
            else (part, "", "")
        segment = Segment(stem, field=F.PATH, channel=Channel.METADATA)
        segment.mentions = self.detector.detect_segment(segment)
        self._merge(segment, knowledge.sweep(segment, False))
        self._merge(segment, sweeper.sweep(segment, None))
        if not any(m.etype not in QUASI_TYPES for m in segment.mentions) \
                and auditor.is_clean(stem, path=True):
            return part
        token = self.secret.mac("filename", f"{state.item.sha256}\x00{prefix}" if is_file
                                else prefix).hex()[:8]
        return f"{'file' if is_file else 'dir'}_{token}{dot}{suffix.lower()}"

    # ------------------------------------------------------------------- report

    def _report(self, states: list[FileState], redactor: Redactor, auditor: Auditor,
                seconds: float) -> RunResult:
        files, records = [], []
        processed = linked = generalized = withheld = 0
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
                if not auditor.is_clean(record.loc):
                    withheld += 1       # a locator built from a key that names someone
                    continue
                linked += record.patient is not None
                generalized += record.generalized
                records.append({"file": output, "loc": record.loc, "patient": record.patient,
                                "status": "generalized" if record.generalized else "sanitized"})
        records.sort(key=lambda r: (r["file"], _natural(r["loc"])))
        sanitized = sum(f["status"] == "sanitized" for f in files)
        counts = {
            "files_total": len(files),
            "files_sanitized": sanitized,
            "files_quarantined": len(files) - sanitized,
            "records_processed": processed,
            "identifiers_detected": sum(n for kind, n in redactor.counts.items()
                                        if kind not in {t.value for t in QUASI_TYPES}),
            "records_linked": linked,
            "records_generalized": generalized,
            "records_suppressed": 0,
            "records_quarantined": withheld,
        }
        return RunResult(files=files, records=records, counts=counts,
                         entities=dict(redactor.counts), seconds=seconds)


def _demographics(evidence, assigned: dict[int, str],
                  contexts: dict[str, PatientContext]) -> list[tuple[str, str, str, int]]:
    """``(patient, district, sex, age)`` of every linked patient with all three known."""
    district: dict[str, set] = defaultdict(set)
    sex: dict[str, set] = defaultdict(set)
    age: dict[str, set] = defaultdict(set)
    for index, item in enumerate(evidence):
        patient = assigned.get(index)
        if not patient:
            continue
        context = contexts[item.record.key]
        if context.district is not None:
            district[patient].add(context.district.latin)
        sex[patient] |= item.summary.sexes
        age[patient] |= {context.reference_year - high for _, high in item.summary.births}
    return [(p, min(district[p]), min(sex[p]), sorted(age[p])[len(age[p]) // 2])
            for p in sorted(age) if district[p] and sex[p] and age[p]]


def _load_name_model(backend: str, lexicon: Lexicon):
    """The learned name check chosen in the policy, or ``None``. It only adds detections,
    so if its packages or files are missing the run continues on the rules alone (the
    pipeline logs that) rather than failing."""
    if backend == "off":
        return None
    try:
        return NameModel(backend, lexicon)
    except (ImportError, OSError, ValueError, KeyError):
        return None


def _digest(policy: Policy) -> str:
    text = json.dumps(dataclasses.asdict(policy), sort_keys=True, default=str)
    return _sha256(text)[:16]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _failure_reason(error: BaseException) -> str:
    if isinstance(error, Terminated):
        return "terminated"
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, MemoryError):
        return "out_of_memory"
    if isinstance(error, crypto.IntegrityError):
        return "integrity_check_failed"
    return "run_failed"


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


def _has_content(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        return True
    return path.is_dir() and any(path.iterdir())


def peak_rss_mb() -> float:
    try:
        import resource
    except ImportError:  # pragma: no cover - not available on Windows
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / 1048576 if sys.platform == "darwin" else usage / 1024, 1)

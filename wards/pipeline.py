"""Run the stages in order, isolating failures to the file that caused them.

    ingest -> parse -> detect -> learn & sweep -> date order -> link -> redact
           -> self-scan & repair -> stage & verify -> publish

A problem in one file quarantines that file only. A problem in a dataset-wide stage
removes everything staged, writes a report marked "failed" and exits non-zero: a partial
release must never look complete.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from wards import dates, documents, ingest, validate, write
from wards.audit import Auditor
from wards.clinical import ConditionPlan
from wards.detect import Detector, resolve
from wards.errors import ConfigError, QuarantineError
from wards.knowledge import Knowledge, id_shape
from wards.lexicon import Lexicon
from wards.link import Linker, record_names
from wards.model import Channel, DocKind, Document, Segment
from wards.model import QUASI_TYPES
from wards.model import EntityType as E
from wards.model import FieldType as F
from wards.policy import Policy
from wards.privacy import Privacy
from wards.redact import PatientContext, Redactor
from wards.release import Release
from wards.security import SafeLog, Secret
from wards.sweep import NameBag, NameSweeper

DEFAULT_REFERENCE_YEAR = 2025
_RESERVE_BYTES = 16 << 20        # freed on failure so that clean-up still has memory
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
        self.linker = Linker(self.lexicon, secret, policy.phone_hub_groups)
        self.secret = secret

    # ------------------------------------------------------------------- driver

    def run(self, input_dir: Path, output_dir: Path) -> RunResult:
        started = time.monotonic()
        input_dir, output_dir = _check_paths(input_dir, output_dir)
        release = Release(output_dir)
        states: list[FileState] = []
        reserve = bytearray(_RESERVE_BYTES)
        try:
            release.open()
            return self._run(input_dir, release, states, started)
        except BaseException:
            del reserve
            release.abort()
            self._report_failure(output_dir, states, time.monotonic() - started)
            raise

    def _run(self, input_dir: Path, release: Release, states: list[FileState],
             started: float) -> RunResult:
        for path in ingest.list_files(input_dir):
            states.append(FileState(ingest.read_file(path, input_dir, self.policy)))
        for state in states:
            state.reason = state.item.reason
        self.log.event("ingested", files=len(states))

        self._each(states, "parse", self._parse, needs_doc=False)
        self._each(states, "detect", lambda s: self.detector.detect_document(s.doc))
        self.log.event("detected", files=sum(s.alive for s in states))

        knowledge = Knowledge(self.lexicon)
        knowledge.learn([s.doc for s in states if s.alive])
        auditor = Auditor(self.lexicon)
        self._each(states, "sweep", lambda s: self._apply_knowledge(s, knowledge, auditor))

        reference_year = self._reference_year(states)
        self._date_orders(states)
        contexts, sweeper = self._link(states, knowledge, reference_year)
        self.log.event("linked", records=len(contexts),
                       patients=len({c.pseudonym for c in contexts.values() if c.pseudonym}))
        self.log.event("privacy", age_band_years=self.privacy.band_width)

        conditions = self._plan_conditions(states, contexts)
        redactor = Redactor(self.lexicon, self.privacy, knowledge)
        self._each(states, "redact", lambda s: self._redact(
            s, redactor, sweeper, conditions, contexts, reference_year, auditor))
        repairs: Counter = Counter()
        self._each(states, "audit", lambda s: repairs.update(found=auditor.repair(s.doc)))
        self._name_outputs(states, knowledge, sweeper, auditor)
        self._each(states, "stage", lambda s: self._stage(s, release, auditor))
        self.log.event("verified", files=sum(s.alive for s in states),
                       repairs=repairs["found"])

        result = self._report(states, redactor, auditor, time.monotonic() - started)
        report = write.build_report(result.files, result.counts, result.entities,
                                    result.seconds, peak_rss_mb())
        release.publish(result.records, report, [s.output for s in states if s.alive])
        self.log.event("done", **{k: result.counts[k] for k in
                                  ("files_total", "files_sanitized", "files_quarantined",
                                   "records_processed", "records_linked")},
                       seconds=round(result.seconds, 2))
        return result

    def _report_failure(self, output_dir: Path, states: list[FileState],
                        seconds: float) -> None:
        """Say plainly that nothing was released. Never hides the original error."""
        try:
            files = [{"input_sha256": s.item.sha256, "output": None, "status": "quarantined",
                      "reason": s.reason or "run_failed"} for s in states]
            counts = dict.fromkeys(_COUNT_NAMES, 0)
            counts.update(files_total=len(files), files_quarantined=len(files))
            (output_dir / "sanitized").mkdir(parents=True, exist_ok=True)
            write.write_report(output_dir / "report.json", write.build_report(
                files, counts, {}, seconds, peak_rss_mb(), status="failed"))
        except Exception as error:  # noqa: BLE001 - report the class only
            self.log.problem("failure_report_not_written", error=error)

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

    def _apply_knowledge(self, state: FileState, knowledge: Knowledge,
                         auditor: Auditor) -> None:
        for record in state.doc.records:
            personal = any(m.etype not in QUASI_TYPES for _, m in record.mentions())
            for segment in record.segments:
                self._merge(segment, knowledge.sweep(segment, personal))
        for segment in state.doc.file_segments():
            self._merge(segment, knowledge.sweep(segment, False))
        auditor.learn(state.doc)

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
        self.privacy.choose_band_width(_demographics(evidence, assigned, contexts))
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
        self.log.event("conditions", suppressed_codes=len(plan.rare))
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
            path = release.add(state.output, write.serialize(state.doc))
            written = self._reread(state.doc, path.read_bytes().decode("utf-8"))
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
                  contexts: dict[str, PatientContext]) -> list[tuple[str, str, int]]:
    """``(district, sex, age)`` of every linked patient for whom all three are known."""
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
    return [(min(district[p]), min(sex[p]), sorted(age[p])[len(age[p]) // 2])
            for p in sorted(age) if district[p] and sex[p] and age[p]]


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

"""Publish a release only once it is complete.

Files are written into a staging folder inside the output folder (never the temporary
folder, which is scanned for personal data and may be another filesystem). Publishing is a
sequence of atomic renames, and ``report.json`` is renamed last: a release without it is
not a release. Only redacted bytes are ever written, so even a killed process leaves no
raw data behind.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from wards import write

STAGE_NAME = ".stage"


class Release:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.stage = output / STAGE_NAME
        self.files = self.stage / "sanitized"
        self._published = False

    def open(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        _remove(self.stage)                  # left over from a run that was killed
        self.files.mkdir(parents=True)

    def path(self, relative: str) -> Path:
        return self.files / relative

    def add(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        write.write_text_file(target, content)
        return target

    def discard(self, relative: str) -> None:
        self.path(relative).unlink(missing_ok=True)

    def publish(self, records: Sequence[dict], report: dict, expected: Sequence[str]) -> None:
        """Move everything into place; the report goes last as the commit marker."""
        wanted = {self.path(relative) for relative in expected}
        for path in wanted:
            if not path.is_file():
                raise RuntimeError("staged_file_missing")
        # nothing that was not declared and verified may be published
        for path in sorted(self.files.rglob("*")):
            if path.is_file() and path not in wanted:
                path.unlink()
        staged_records = self.stage / "records.jsonl"
        staged_report = self.stage / "report.json"
        write.write_records(staged_records, records)
        write.write_report(staged_report, report)
        target = self.output / "sanitized"
        if target.is_dir():
            target.rmdir()                   # only ever an empty directory
        self._published = True
        os.replace(self.files, target)
        os.replace(staged_records, self.output / "records.jsonl")
        os.replace(staged_report, self.output / "report.json")
        _remove(self.stage)

    def abort(self) -> None:
        """Remove everything this run wrote. Safe to call more than once."""
        _remove(self.stage)
        if self._published:
            _remove(self.output / "sanitized")
            for name in ("records.jsonl", "report.json"):
                (self.output / name).unlink(missing_ok=True)
        self._published = False


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)

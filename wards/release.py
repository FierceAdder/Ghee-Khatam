"""Publish a release only once it is complete.

Files wait for the audit in a staging folder inside the output folder (never the temporary
folder, which is scanned for personal data), and only as AES-GCM ciphertext
(:mod:`wards.crypto`) under their output names, which are audited before: contents that have
not passed the audit are never on disk in the clear. Publishing writes each verified file to
a temporary name inside its final folder and renames it; ``report.json`` is renamed last: a
release without it is not a release. So a copy of the output folder taken at any moment
holds only ciphertext and audited files. The staging folder and temporary files are removed
on every way out of the run.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from wards import crypto, write
from wards.ingest import InputFile
from wards.security import Secret, uninterrupted

STAGE_NAME = ".stage"
PART_PREFIX = ".wards-part-"          # a file being written, before its rename
QUARANTINE_NAME = "quarantine"        # optional, encrypted, not part of the release
LOG_PATH = "logs/run.jsonl"


class Release:
    def __init__(self, output: Path, secret: Secret | None = None) -> None:
        """Without a secret the staging key is purely random (it is per-process anyway)."""
        self.output = output
        self.stage = output / STAGE_NAME
        self.files = self.stage / "sanitized"
        self._cipher = crypto.StageCipher(secret)      # refuses to run without encryption
        self._parts: set[Path] = set()
        self._published = False
        self._kept_quarantine = False

    def open(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        self.close()                         # left over from a run that was killed
        self.files.mkdir(parents=True)

    def path(self, relative: str) -> Path:
        """Where the ciphertext of ``relative`` waits."""
        return self.files / relative

    def add(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        data = content.encode("utf-8", errors="replace")
        write.write_text_file(target, self._cipher.seal(relative, data))
        return target

    def read(self, relative: str) -> str:
        """A staged file as it is on disk, decrypted in memory."""
        return self._decrypt(relative).decode("utf-8")

    def _decrypt(self, relative: str) -> bytes:
        return self._cipher.open(relative, self.path(relative).read_bytes())

    def discard(self, relative: str) -> None:
        self.path(relative).unlink(missing_ok=True)

    def keep_quarantined(self, secret: Secret, source: Path | crypto.Archive,
                         files: Sequence[tuple[InputFile, str]]) -> int:
        """Keep an encrypted copy of each ``(input file, reason)`` in ``quarantine/``."""
        if not files:
            return 0
        folder = self.output / QUARANTINE_NAME
        if folder.is_dir():
            folder.rmdir()                   # only ever an empty directory
        self._kept_quarantine = True
        writer = crypto.ArchiveWriter(folder, secret, crypto.QUARANTINE)
        for item, reason in files:
            crypto.keep_file(writer, source, item, reason)
        writer.close()
        return len(files)

    def publish(self, records: Sequence[dict], report: dict, expected: Sequence[str],
                log: str | None = None) -> None:
        """Move everything into place; the report goes last as the commit marker."""
        for relative in expected:
            if not self.path(relative).is_file():
                raise RuntimeError("staged_file_missing")
        target = self.output / "sanitized"
        if target.is_dir():
            target.rmdir()                   # only ever an empty directory
        self._published = True
        target.mkdir()
        # nothing that was not declared and verified is ever decrypted into the release
        for relative in expected:
            data = self._decrypt(relative)
            final = target / relative
            final.parent.mkdir(parents=True, exist_ok=True)
            self._commit(final, lambda part, data=data: part.write_bytes(data))
        self._commit(self.output / "records.jsonl",
                     lambda part: write.write_records(part, records))
        if log is not None:
            (self.output / LOG_PATH).parent.mkdir(exist_ok=True)
            self._commit(self.output / LOG_PATH, lambda part: write.write_text_file(part, log))
        self._commit(self.output / "report.json",
                     lambda part: write.write_report(part, report))
        self.close()

    def _commit(self, final: Path, writer: Callable[[Path], object]) -> None:
        """Write audited content beside ``final``, then rename it into place."""
        part = final.with_name(PART_PREFIX + os.urandom(8).hex())
        self._parts.add(part)
        writer(part)
        os.replace(part, final)
        self._parts.discard(part)

    def close(self) -> None:
        """Remove the stage and any half-written file. Safe to call on every way out."""
        with uninterrupted():
            stale = [*self.output.glob(PART_PREFIX + "*"),
                     *(self.output / LOG_PATH).parent.glob(PART_PREFIX + "*")]
            for part in sorted(self._parts.union(stale)):
                _remove(part)
            self._parts.clear()
            _remove(self.stage)

    def abort(self) -> None:
        """Remove everything this run wrote. Safe to call more than once."""
        with uninterrupted():
            self.close()
            if self._published:
                _remove(self.output / "sanitized")
                for name in ("records.jsonl", "report.json", LOG_PATH):
                    (self.output / name).unlink(missing_ok=True)
            if self._kept_quarantine:
                _remove(self.output / QUARANTINE_NAME)
            self._published = self._kept_quarantine = False


def _remove(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:                      # clean-up must not hide the original failure
        pass

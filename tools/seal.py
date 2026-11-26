#!/usr/bin/env python3
"""Encrypt a folder of records, so that raw data rests only as ciphertext.

    RAKSHA_SECRET=... python3 tools/seal.py --in <records folder> --out <new empty folder>
    RAKSHA_SECRET=... python3 main.py --sealed <that folder> --output <empty output dir>

To inspect the files a run quarantined (``main.py --encrypt-quarantine``), which writes
their original, personal content in the clear to a folder of your choice:

    RAKSHA_SECRET=... python3 tools/seal.py --open-quarantine --in <output>/quarantine \\
        --out <new empty folder>

The ``--in`` folder is only read. Keys come from the secret and are never written, so keep
the secret somewhere else than the data. Exit codes: 0 done, 2 could not start,
1 failure. Nothing printed here contains file names or content.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

from wards import crypto  # noqa: E402
from wards.errors import ConfigError  # noqa: E402
from wards.policy import Policy  # noqa: E402
from wards.security import SafeLog, Secret, Terminated, harden_process, uninterrupted  # noqa: E402


class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would echo the offending argument
        raise ConfigError("invalid_arguments")


def seal(source: Path, target: Path, secret: Secret) -> tuple[int, int]:
    """``(files sealed, files recorded without content)``."""
    writer = crypto.ArchiveWriter(target, secret, crypto.SEALED_INPUT)
    crypto.seal_tree(source, writer, Policy())
    writer.close()
    entries = crypto.Archive.open(target, secret).entries
    sealed = sum(bool(e.name) for e in entries)
    return sealed, len(entries) - sealed


def open_quarantine(source: Path, target: Path, secret: Secret) -> tuple[int, int]:
    archive = crypto.Archive.open(source, secret, crypto.QUARANTINE)
    opened = 0
    for entry in archive.entries:
        if not entry.name:
            continue
        path = target / entry.path
        data = archive.read(entry)                    # verified before anything is written
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
            out.write(data)
        opened += 1
    return opened, len(archive.entries) - opened


def _check_folders(source: Path, target: Path, quarantine: bool) -> tuple[Path, Path]:
    if not source.is_dir():
        raise ConfigError("input_is_not_a_directory")
    source, target = source.resolve(), target.resolve()
    # a quarantine is opened outside the output folder, which may be shared as a release
    keep_out = source.parent if quarantine else source
    if target == keep_out or keep_out in target.parents or target in source.parents:
        raise ConfigError("input_and_output_overlap")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ConfigError("output_is_not_an_empty_directory")
    return source, target


def main(argv: list[str] | None = None) -> int:
    harden_process()
    log = SafeLog()
    errors = SafeLog(sys.stderr)
    try:
        parser = _QuietParser(description="Encrypt a folder of records")
        parser.add_argument("--in", dest="source", required=True)
        parser.add_argument("--out", dest="target", required=True)
        parser.add_argument("--open-quarantine", action="store_true",
                            help="decrypt an <output>/quarantine folder instead of sealing")
        args = parser.parse_args(argv)
        secret = Secret.from_env()
        crypto.require()
        source, target = _check_folders(Path(args.source), Path(args.target),
                                        args.open_quarantine)
        existed = target.exists()
        try:
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            action = open_quarantine if args.open_quarantine else seal
            done, without_content = action(source, target, secret)
        except BaseException:
            with uninterrupted():                     # leave nothing half-made behind
                _empty(target, remove=not existed)
            raise
        log.event("opened" if args.open_quarantine else "sealed", files=done,
                  without_content=without_content)
        return 0
    except ConfigError as error:
        errors.problem("cannot_start", reason=str(error))
        return 2
    except KeyboardInterrupt:
        errors.problem("interrupted")
        return 130
    except Terminated:
        errors.problem("terminated")
        return 143
    except crypto.IntegrityError as error:
        errors.problem("failed", reason=str(error))
        return 1
    except Exception as error:  # noqa: BLE001 - never let a traceback print values
        errors.problem("failed", error=error)
        return 1


def _empty(folder: Path, remove: bool) -> None:
    """``folder`` was new or empty when we started, so everything in it is ours."""
    if remove:
        shutil.rmtree(folder, ignore_errors=True)
        return
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())

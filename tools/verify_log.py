#!/usr/bin/env python3
"""Check that a release's run log is complete and unedited.

    RAKSHA_SECRET=... python3 tools/verify_log.py <output folder>

Every line of ``logs/run.jsonl`` carries an HMAC over the line before it, keyed with the
release secret, and the last line seals the line count and the SHA-256 of ``report.json``.
An edited, removed, added or reordered line, or a swapped report, fails the check.
Exit codes: 0 intact, 1 not intact, 2 could not check.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wards.errors import ConfigError  # noqa: E402
from wards.security import Secret, verify_log  # noqa: E402


def check(output: Path, secret: Secret) -> bool:
    log = (output / "logs" / "run.jsonl").read_text(encoding="utf-8")
    if not verify_log(log, secret):
        return False
    seal = json.loads(log.splitlines()[-1])
    report = hashlib.sha256((output / "report.json").read_bytes()).hexdigest()
    return seal.get("report") == report


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: verify_log.py <output folder>", file=sys.stderr)
        return 2
    try:
        intact = check(Path(argv[0]), Secret.from_env())
    except (OSError, ConfigError, ValueError):
        print("could not check the log", file=sys.stderr)
        return 2
    print("log intact" if intact else "log NOT intact")
    return 0 if intact else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

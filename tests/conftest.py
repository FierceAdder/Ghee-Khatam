"""Shared fixtures. All names and numbers in these tests are invented."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wards.detect import Detector  # noqa: E402
from wards.lexicon import Lexicon  # noqa: E402
from wards.pipeline import Pipeline  # noqa: E402
from wards.policy import load_policy  # noqa: E402
from wards.security import SafeLog, Secret  # noqa: E402


@pytest.fixture(scope="session")
def lexicon() -> Lexicon:
    return Lexicon.load()


@pytest.fixture(scope="session")
def bare_lexicon(lexicon) -> Lexicon:
    """No name gazetteer at all: proves that context rules work on unseen names."""
    return replace(lexicon, first_names=frozenset(), surnames=frozenset(),
                   first_spellings=frozenset(), surname_spellings=frozenset())


@pytest.fixture(scope="session")
def detector(lexicon) -> Detector:
    return Detector(lexicon)


@pytest.fixture(scope="session")
def policy():
    return load_policy(ROOT / "config" / "policy.yaml")


@pytest.fixture
def secret() -> Secret:
    return Secret(b"unit-test-secret")


@dataclass
class Release:
    root: Path
    report: dict
    records: list
    log: str

    def entry(self, source: Path) -> dict:
        import hashlib

        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return next(e for e in self.report["files"] if e["input_sha256"] == digest)

    def text(self, relative: str) -> str:
        return (self.root / "sanitized" / relative).read_text(encoding="utf-8")

    def everything(self) -> str:
        """All released bytes and logs, including output file names."""
        parts = [self.log]
        for path in sorted(self.root.rglob("*")):
            parts.append(path.name)
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def claims(self) -> dict:
        return {(r["file"].removeprefix("sanitized/"), r["loc"]): r["patient"]
                for r in self.records}


def write_tree(root: Path, files: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


@pytest.fixture
def release(tmp_path, policy, lexicon):
    """Run the whole pipeline over ``{relative path: content}``."""
    counter = iter(range(1000))

    def run(files: dict, secret: bytes = b"unit-test-secret", **overrides) -> Release:
        number = next(counter)
        source = write_tree(tmp_path / f"in{number}", files)
        out = tmp_path / f"out{number}"
        stream = io.StringIO()
        pipeline = Pipeline(replace(policy, **overrides), Secret(secret), SafeLog(stream), lexicon)
        pipeline.run(source, out)
        records = [json.loads(line) for line in
                   (out / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        return Release(out, report, records, stream.getvalue())

    return run

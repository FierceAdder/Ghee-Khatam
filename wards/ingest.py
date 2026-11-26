"""Stage 0: walk the input, identify each file by content and decode it.

Every input file ends up in exactly one of two places: an :class:`InputFile` that will be
processed, or one with a ``reason`` for quarantine. Nothing is ever skipped silently and
nothing is opened for writing.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from wards import jsonio
from wards.errors import QuarantineError
from wards.model import DocKind
from wards.policy import Policy

TEXT_EXTENSIONS = frozenset({".txt", ".text", ".log", ".md"})
TABLE_EXTENSIONS = frozenset({".csv", ".tsv"})
JSON_EXTENSIONS = frozenset({".json"})

_BINARY_MAGIC = (
    b"%PDF", b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"PK\x03\x04", b"PK\x05\x06",
    b"\x1f\x8b", b"\xd0\xcf\x11\xe0", b"BM6", b"II*\x00", b"MM\x00*", b"\x7fELF",
    b"7z\xbc\xaf", b"Rar!", b"SQLite format 3",
)
_XML_START = re.compile(r"^<(\?xml|!DOCTYPE|!--|[A-Za-z_][\w.:-]*[\s/>])")
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1a\x1c-\x1f]")
_HIGH_RUN = re.compile(rb"[\x80-\xff]{3,}")


@dataclass
class InputFile:
    rel: str
    sha256: str
    text: str = ""
    kind: DocKind | None = None
    encoding: str = ""
    delimiter: str = ","
    reason: str = ""                 # non-empty means quarantine

    @property
    def quarantined(self) -> bool:
        return bool(self.reason)


def list_files(root: Path) -> list[Path]:
    """Every file under ``root`` in a stable order. Directory links are not followed."""
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        subdirectories.sort()
        base = Path(directory)
        found.extend(base / name for name in names)
        found.extend(base / d for d in subdirectories if (base / d).is_symlink())
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def read_file(path: Path, root: Path, policy: Policy) -> InputFile:
    rel = path.relative_to(root).as_posix()
    if path.is_symlink() or not path.is_file():
        # Following a link could pull in data from outside the input folder.
        digest = hashlib.sha256(os.fsencode(rel)).hexdigest()
        try:
            if path.is_file():
                digest = _sha256(path)
        except OSError:
            pass
        return InputFile(rel=rel, sha256=digest, reason="not_a_regular_file")
    try:
        if path.stat().st_size > policy.max_file_mb * 1024 * 1024:
            return InputFile(rel=rel, sha256=_sha256(path), reason="file_too_large")
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return InputFile(rel=rel, sha256=hashlib.sha256(os.fsencode(rel)).hexdigest(),
                         reason="unreadable_file")
    item = InputFile(rel=rel, sha256=hashlib.sha256(raw).hexdigest())
    try:
        classify(item, raw, path.suffix.lower(), policy)
    except QuarantineError as error:
        item.reason, item.text, item.kind = error.reason, "", None
    return item


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(item: InputFile, raw: bytes, suffix: str, policy: Policy) -> None:
    """Fill in kind, text and encoding, or raise :class:`QuarantineError`."""
    if not raw:
        raise QuarantineError("empty_file")
    if raw.startswith(_BINARY_MAGIC):
        raise QuarantineError("unsupported_format")
    text, encoding = decode(raw)
    stripped = text.strip()
    if not stripped:
        raise QuarantineError("empty_file")
    if _CONTROL.search(text):
        raise QuarantineError("binary_content")
    item.text, item.encoding = text, encoding
    if suffix == ".xml" or _XML_START.match(stripped):
        raise QuarantineError("unsupported_format")

    looks_json = stripped[0] in "{["
    if suffix in JSON_EXTENSIONS:
        _require_json(stripped, policy)
        item.kind = DocKind.JSON
    elif suffix in TABLE_EXTENSIONS:
        if looks_json and _parses_as_json(stripped, policy):
            item.kind = DocKind.JSON
        else:
            item.kind = DocKind.CSV
            item.delimiter = sniff_delimiter(text, suffix)
    elif suffix in TEXT_EXTENSIONS:
        item.kind = DocKind.JSON if looks_json and _parses_as_json(stripped, policy) \
            else DocKind.TEXT
    elif looks_json and _parses_as_json(stripped, policy):
        item.kind = DocKind.JSON
    else:
        raise QuarantineError("unsupported_format")


def _require_json(text: str, policy: Policy) -> None:
    if jsonio.depth(text) > policy.max_json_depth:
        raise QuarantineError("nesting_too_deep")
    try:
        jsonio.loads(text)
    except (ValueError, RecursionError):
        raise QuarantineError("malformed_json") from None


def _parses_as_json(text: str, policy: Policy) -> bool:
    if jsonio.depth(text) > policy.max_json_depth:
        # A WhatsApp export also starts with "["; only real JSON is rejected for depth.
        if text.startswith(("{", "[{", "[[")):
            raise QuarantineError("nesting_too_deep")
        return False
    try:
        jsonio.loads(text)
    except (ValueError, RecursionError):
        return False
    return True


def decode(raw: bytes) -> tuple[str, str]:
    """Decode UTF-8, UTF-16 or cp1252. Anything doubtful is quarantined."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return _strict(raw[3:], "utf-8"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return _strict(raw, "utf-32"), "utf-32"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return _strict(raw, "utf-16"), "utf-16"
    if b"\x00" in raw:
        # UTF-16 without a byte order mark: ASCII text alternates with NUL bytes.
        sample = raw[:4000]
        even, odd = sample[0::2].count(0), sample[1::2].count(0)
        half = max(len(sample) // 2, 1)
        if len(raw) % 2 == 0:
            if odd > half * 0.3 and even < half * 0.05:
                return _plausible(_strict(raw, "utf-16-le")), "utf-16-le"
            if even > half * 0.3 and odd < half * 0.05:
                return _plausible(_strict(raw, "utf-16-be")), "utf-16-be"
        raise QuarantineError("binary_content")
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # cp1252 maps almost every byte, so accept it only for text that looks like Western
    # legacy data: isolated accented letters or punctuation, never long high-byte runs
    # (which indicate damaged UTF-8 or a legacy Indic encoding).
    if _HIGH_RUN.search(raw) or b"\xfe" in raw or b"\xff\xfe" in raw:
        raise QuarantineError("undecodable_text")
    return _strict(raw, "cp1252"), "cp1252"


def _plausible(text: str) -> str:
    """Reject a guessed encoding unless the result looks like English or Hindi text."""
    sample = text[:4000]
    readable = sum(c.isascii() or "\u0900" <= c <= "\u097f" for c in sample)
    if readable < 0.9 * len(sample):
        raise QuarantineError("binary_content")
    return text


def _strict(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding)
    except (UnicodeError, ValueError):
        raise QuarantineError("undecodable_text") from None


def sniff_delimiter(text: str, suffix: str) -> str:
    """Pick the delimiter that splits the header and following lines most consistently."""
    lines = [line for line in text.split("\n", 50)[:50] if line.strip()]
    if not lines:
        return "\t" if suffix == ".tsv" else ","
    default = "\t" if suffix == ".tsv" else ","
    candidates = [default] + [d for d in (",", "\t", ";", "|") if d != default]

    def score(delimiter: str) -> tuple[int, int]:
        expected = _count_outside_quotes(lines[0], delimiter)
        if expected == 0:
            return 0, 0
        consistent = sum(_count_outside_quotes(line, delimiter) == expected for line in lines)
        return consistent, expected

    best = max(candidates, key=score)   # ties keep the first (extension) candidate
    return best if score(best) > (0, 0) else default


def _count_outside_quotes(line: str, delimiter: str) -> int:
    return "".join(line.split('"')[0::2]).count(delimiter)

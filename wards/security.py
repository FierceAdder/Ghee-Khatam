"""Secret handling and PII-free logging.

Two rules hold across the whole code base:

* ``RAKSHA_SECRET`` is read once, kept inside :class:`Secret` and never formatted,
  logged, written or attached to an exception.
* Nothing printed may contain a value read from the input. :class:`SafeLog` therefore
  accepts only fixed event names, numbers, exception class names and hash prefixes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import sys
import threading
import time
import warnings
from collections.abc import Mapping
from typing import TextIO

from wards import logschema
from wards.errors import ConfigError

SECRET_ENV = "RAKSHA_SECRET"

_SAFE_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_HEX_PREFIX = re.compile(r"^[0-9a-f]{1,16}$")


class Secret:
    """The per-release key. Only HMAC digests ever leave this object."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ConfigError("secret_is_empty")
        self._key = key

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Secret":
        env = os.environ if environ is None else environ
        value = env.get(SECRET_ENV, "")
        if not value:
            # Running unkeyed would produce guessable pseudonyms and date shifts.
            raise ConfigError("raksha_secret_not_set")
        return cls(value.encode("utf-8", errors="surrogateescape"))

    def mac(self, domain: str, value: str) -> bytes:
        """HMAC-SHA256 of ``value``, domain separated so different uses never collide."""
        message = domain.encode("utf-8") + b"\x00" + value.encode("utf-8", errors="surrogatepass")
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def mac_int(self, domain: str, value: str) -> int:
        return int.from_bytes(self.mac(domain, value)[:8], "big")

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):  # pragma: no cover - defensive
        raise TypeError("Secret cannot be serialised")


class SafeLog:
    """Logger that cannot print input content.

    ``record`` is the structured log: every event is checked against
    :mod:`wards.logschema`, summary events are also printed, and :meth:`sealed` returns the
    whole log as JSON Lines in which each line carries an HMAC over the previous one, so an
    edited, removed or reordered line is detected by :func:`verify_log`.
    """

    MAX_LINES = 50_000

    def __init__(self, stream: TextIO | None = None, level: str = logschema.SUMMARY,
                 strict: bool = False) -> None:
        if level not in (logschema.SUMMARY, logschema.DETAIL):
            raise ConfigError("invalid_log_level")
        self._stream = stream
        self.level = level
        self.strict = strict           # tests: refuse; real runs: drop and count
        self.dropped = 0
        self.overflow = 0
        self._lines: list[dict] = []
        self._started = time.monotonic()

    def record(self, event: str, **fields) -> None:
        problem = logschema.check(event, fields)
        if problem is not None:
            if self.strict:
                raise TypeError(f"log schema: {problem}")
            self.dropped += 1
            return
        summary = logschema.level_of(event) == logschema.SUMMARY
        if not summary and self.level != logschema.DETAIL:
            return
        if len(self._lines) >= self.MAX_LINES:
            self.overflow += 1
        else:
            self._lines.append({"event": event,
                                "t": round(time.monotonic() - self._started, 3), **fields})
        if summary:
            self._write(" ".join([event] + [f"{k}={fields[k]}" for k in sorted(fields)]))

    def sealed(self, secret: "Secret", report_sha256: str) -> str:
        """The log as JSON Lines, chained with HMACs and closed by a seal line."""
        lines = [*self._lines, {"event": "log", "dropped": self.dropped,
                                "overflow": self.overflow},
                 {"event": "seal", "lines": len(self._lines) + 1, "report": report_sha256}]
        out, previous = [], ""
        for number, line in enumerate(lines):
            body = _canonical(dict(line, seq=number))
            previous = secret.mac("log", previous + "\n" + body).hex()[:32]
            out.append(_canonical(dict(line, seq=number, mac=previous)))
        return "\n".join(out) + "\n"

    def event(self, name: str, **numbers: int | float) -> None:
        parts = [_checked_token(name)]
        for key in sorted(numbers):
            value = numbers[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("SafeLog.event accepts numbers only")
            parts.append(f"{_checked_token(key)}={value}")
        self._write(" ".join(parts))

    def problem(self, name: str, sha256: str = "", error: BaseException | None = None,
                reason: str = "") -> None:
        """Report a failure using only a hash prefix, a reason code and the error class."""
        parts = [_checked_token(name)]
        prefix = sha256[:12]
        if prefix:
            parts.append(f"file={prefix if _HEX_PREFIX.match(prefix) else 'unknown'}")
        if reason:
            parts.append(f"reason={_checked_token(reason)}")
        if error is not None:
            parts.append(f"error={type(error).__name__}")
        self._write(" ".join(parts))

    def _write(self, line: str) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        print(line, file=stream, flush=True)


def _canonical(line: dict) -> str:
    return json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def verify_log(text: str, secret: Secret) -> bool:
    """True if every line of a sealed log is intact, in order, and nothing is missing."""
    previous, lines = "", text.splitlines()
    try:
        for number, raw in enumerate(lines):
            line = json.loads(raw)
            mac = line.pop("mac")
            if line.get("seq") != number:
                return False
            previous_expected = secret.mac("log", previous + "\n" + _canonical(line)).hex()[:32]
            if not hmac.compare_digest(mac, previous_expected):
                return False
            previous = mac
        last = json.loads(lines[-1])
        return last.get("event") == "seal" and last.get("lines") == len(lines) - 1
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


def _checked_token(value: str) -> str:
    return value if _SAFE_TOKEN.match(value) else "redacted"


class Terminated(BaseException):
    """The run was asked to stop (SIGTERM); staged files must still be removed."""


def harden_process() -> None:
    """Keep values off the console and out of crash dumps, and stop cleanly."""
    warnings.simplefilter("ignore")

    def _hook(exc_type, _value, _traceback) -> None:
        sys.stderr.write(f"fatal error={exc_type.__name__}\n")

    sys.excepthook = _hook
    _disable_core_dumps()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)


def _terminate(_signum, _frame) -> None:
    raise Terminated()


def _disable_core_dumps() -> None:
    """A core dump would write every record held in memory to disk."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):  # not available on this platform
        pass

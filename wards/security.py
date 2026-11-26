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
import os
import re
import sys
import warnings
from collections.abc import Mapping
from typing import TextIO

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
    """Logger that cannot print input content."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

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


def _checked_token(value: str) -> str:
    return value if _SAFE_TOKEN.match(value) else "redacted"


def harden_process() -> None:
    """Stop the interpreter from printing values on its own (warnings, tracebacks)."""
    warnings.simplefilter("ignore")

    def _hook(exc_type, _value, _traceback) -> None:
        sys.stderr.write(f"fatal error={exc_type.__name__}\n")

    sys.excepthook = _hook

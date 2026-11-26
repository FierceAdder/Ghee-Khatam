"""Exception types.

Messages carried by these exceptions are fixed, PII-free codes. Never build one from file
content: stdout, stderr and the report are all scanned for personal data.
"""

from __future__ import annotations


class WardsError(Exception):
    """Base class for errors raised on purpose by the firewall."""


class ConfigError(WardsError):
    """The run cannot start: bad arguments, missing secret or invalid policy."""


class QuarantineError(WardsError):
    """One input file cannot be processed safely and must not be exported.

    ``reason`` is a short snake_case code that is written to ``report.json``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

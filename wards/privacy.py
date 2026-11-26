"""Quasi-identifier transforms: per-patient date shifts, age bands, PIN prefixes.

Every transform keeps the data truthful or consistent: one patient's dates all move by the
same secret offset (so stay lengths are exact) and an age band always contains the age.
"""

from __future__ import annotations

import datetime as dt

from wards.dates import DateMatch
from wards.policy import Policy
from wards.security import Secret


class Privacy:
    def __init__(self, policy: Policy, secret: Secret) -> None:
        self.policy = policy
        self.secret = secret

    def shift_days(self, key: str) -> int:
        """Uniform in ``[-max, -1] ∪ [1, max]``; the same key always moves the same way."""
        limit = self.policy.date_shift_max_days
        offset = self.secret.mac_int("date-shift", key) % (2 * limit) - limit
        return offset if offset < 0 else offset + 1

    def shifted(self, match: DateMatch, order: str, offset: int,
                reference_year: int) -> str | None:
        """The date moved by ``offset`` days in its original format, or ``None``."""
        value = match.resolve(order, reference_year)
        if value is None:
            return None
        try:
            return match.render(value + dt.timedelta(days=offset), order)
        except OverflowError:
            return None

    def date_of_birth(self, match: DateMatch | None, order: str, offset: int,
                      reference_year: int, top_coded: bool) -> str | None:
        """Replacement for a birth date; ``None`` means it must be removed entirely.

        A birth date would undo the age top-code, so it is always removed for the oldest
        patients.
        """
        if match is None or top_coded or self.policy.dob_mode == "remove":
            return None
        value = match.resolve(order, reference_year)
        if value is None or reference_year - value.year >= self.policy.age_top_code:
            return None
        if self.policy.dob_mode == "year":
            return f"{value.year:04d}"
        return self.shifted(match, order, offset, reference_year)

    def age_band(self, age: int) -> str:
        top, width = self.policy.age_top_code, self.policy.age_band_years
        if age >= top:
            return f"{top}+"
        low = (age // width) * width
        return f"{low}-{min(low + width - 1, top - 1)}"

    def pin_prefix(self, value: str) -> str:
        """``123456`` -> ``123***`` (all of it hidden when the prefix length is 0)."""
        digits = "".join(c for c in value if c.isdigit())
        keep = self.policy.pin_prefix_digits
        if keep == 0 or len(digits) != 6:
            return "[PIN]"
        return digits[:keep] + "*" * (6 - keep)

    def pin_number(self, value: str) -> str:
        """Type-preserving form for a PIN stored as a JSON number."""
        digits = "".join(c for c in value if c.isdigit())
        keep = self.policy.pin_prefix_digits
        return digits[:keep] if keep and len(digits) == 6 else "0"

"""Quasi-identifier transforms: per-patient date shifts, age bands, PIN prefixes.

Every transform keeps the data truthful or consistent: one patient's dates all move by the
same secret offset (so stay lengths are exact) and an age band always contains the age.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence

from wards.dates import DateMatch
from wards.policy import Policy
from wards.security import Secret


class Privacy:
    def __init__(self, policy: Policy, secret: Secret) -> None:
        self.policy = policy
        self.secret = secret
        self.band_width = policy.age_band_years

    def choose_band_width(self, patients: Sequence[tuple[str, str, int]]) -> int:
        """One age-band width for the whole release, from ``(district, sex, age)`` triples.

        Someone who knows the population looks for a person by district, sex and age. A
        group is expected to hold ``registry_per_patient`` people for every patient seen in
        it. Bands widen, for everyone alike, until few enough patients are in groups smaller
        than ``min_group_size``. Large datasets keep narrow bands; small ones cannot.
        """
        policy = self.policy
        widths = list(range(policy.age_band_years, policy.age_band_max_years + 1,
                            policy.age_band_years))
        if widths[-1] != policy.age_band_max_years:
            widths.append(policy.age_band_max_years)
        self.band_width = widths[-1] if patients else widths[0]
        for width in widths:
            groups = Counter((district, sex, min(age, policy.age_top_code) // width)
                             for district, sex, age in patients)
            exposed = sum(count for count in groups.values()
                          if count * policy.registry_per_patient < policy.min_group_size)
            if exposed * 100 <= policy.small_group_percent * len(patients):
                self.band_width = width
                break
        return self.band_width

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
        top, width = self.policy.age_top_code, self.band_width
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

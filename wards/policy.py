"""Privacy policy loading and validation.

``config/policy.yaml`` uses only flat ``key: value`` lines, so no YAML dependency is
needed. Unknown keys and out-of-range values stop the run: silently ignoring a typo in a
privacy rule would be worse than failing.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from wards.errors import ConfigError

DOB_MODES = ("shift", "year", "remove")


@dataclass(frozen=True)
class Policy:
    age_band_years: int = 5
    age_top_code: int = 90
    date_shift_max_days: int = 180
    dob_mode: str = "remove"
    pin_prefix_digits: int = 3
    phone_hub_groups: int = 3
    min_group_size: int = 5
    max_file_mb: int = 200
    max_json_depth: int = 200

    def validate(self) -> "Policy":
        checks = {
            "age_band_years": 1 <= self.age_band_years <= 20,
            "age_top_code": 60 <= self.age_top_code <= 120,
            # Above 365 days a "shifted" DOB would stop being informative.
            "date_shift_max_days": 1 <= self.date_shift_max_days <= 365,
            "dob_mode": self.dob_mode in DOB_MODES,
            "pin_prefix_digits": 0 <= self.pin_prefix_digits <= 3,
            "phone_hub_groups": 2 <= self.phone_hub_groups <= 100,
            "min_group_size": 1 <= self.min_group_size <= 100,
            "max_file_mb": 1 <= self.max_file_mb <= 2000,
            "max_json_depth": 16 <= self.max_json_depth <= 500,
        }
        for name, ok in checks.items():
            if not ok:
                raise ConfigError(f"policy_value_out_of_range.{name}")
        return self


def load_policy(path: Path) -> Policy:
    if not path.is_file():
        raise ConfigError("policy_file_missing")
    known = {f.name: f.type for f in fields(Policy)}
    values: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ConfigError("policy_line_without_colon")
        key, value = (part.strip() for part in line.split(":", 1))
        if key not in known:
            raise ConfigError("policy_unknown_key")
        value = value.strip("\"'")
        if str(known[key]) == "int":
            try:
                values[key] = int(value)
            except ValueError:
                raise ConfigError(f"policy_value_not_integer.{key}") from None
        else:
            values[key] = value
    return Policy(**values).validate()

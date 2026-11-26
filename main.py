#!/usr/bin/env python3
"""Secret Wards privacy firewall.

    RAKSHA_SECRET=... python3 main.py --input <input dir> --output <empty output dir>

Exit codes: 0 release complete, 2 the run could not start, 1 unexpected failure.
Nothing printed here ever contains input content.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True      # the repository may be mounted read-only

from wards.errors import ConfigError  # noqa: E402
from wards.pipeline import Pipeline  # noqa: E402
from wards.policy import load_policy  # noqa: E402
from wards.security import SafeLog, Secret, Terminated, harden_process  # noqa: E402


class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would echo the offending argument
        raise ConfigError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    harden_process()
    log = SafeLog()
    errors = SafeLog(sys.stderr)
    try:
        parser = _QuietParser(description="Secret Wards privacy firewall")
        parser.add_argument("--input", default=os.environ.get("RAKSHA_INPUT"))
        parser.add_argument("--output", default=os.environ.get("RAKSHA_OUTPUT"))
        parser.add_argument("--policy", default=str(HERE / "config" / "policy.yaml"))
        args = parser.parse_args(argv)
        if not args.input or not args.output:
            raise ConfigError("input_and_output_required")
        secret = Secret.from_env()
        pipeline = Pipeline(load_policy(Path(args.policy)), secret, log)
        pipeline.run(Path(args.input), Path(args.output))
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
    except Exception as error:  # noqa: BLE001 - never let a traceback print values
        errors.problem("run_failed", error=error)
        return 1


if __name__ == "__main__":
    sys.exit(main())

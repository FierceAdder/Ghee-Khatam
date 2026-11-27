#!/usr/bin/env python3
"""Secret Wards privacy firewall.

    RAKSHA_SECRET=... python3 main.py --input <input dir> --output <empty output dir>
    RAKSHA_SECRET=... python3 main.py --sealed <dir made by tools/seal.py> --output <dir>

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
# Some onnxruntime builds collect usage data: files in TMPDIR and HOME (which are scanned
# for leftovers) and network calls. It must be off before the package is imported.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

from wards import crypto  # noqa: E402
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
        parser.add_argument("--input")
        parser.add_argument("--sealed",
                            help="instead of --input: a folder made by tools/seal.py, "
                                 "decrypted in memory only")
        parser.add_argument("--output", default=os.environ.get("RAKSHA_OUTPUT"))
        parser.add_argument("--encrypt-quarantine", action="store_true",
                            help="keep an encrypted copy of each quarantined file in "
                                 "<output>/quarantine (off by default)")
        parser.add_argument("--policy", default=str(HERE / "config" / "policy.yaml"))
        parser.add_argument("--log-level", default="summary", choices=("summary", "detail"),
                            help="detail adds per-file counts to logs/run.jsonl")
        args = parser.parse_args(argv)
        if args.sealed is not None and args.input is not None:
            raise ConfigError("input_and_sealed_both_given")
        source = args.sealed or (os.environ.get("RAKSHA_INPUT") if args.input is None
                                 else args.input)
        if not source or not args.output:
            raise ConfigError("input_and_output_required")
        secret = Secret.from_env()
        crypto.require()            # never stage plaintext because a package is missing
        log = SafeLog(level=args.log_level)
        pipeline = Pipeline(load_policy(Path(args.policy)), secret, log)
        pipeline.run(Path(source), Path(args.output), sealed=args.sealed is not None,
                     keep_quarantine=args.encrypt_quarantine)
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
    except crypto.IntegrityError as error:
        errors.problem("run_failed", reason=str(error))
        return 1
    except Exception as error:  # noqa: BLE001 - never let a traceback print values
        errors.problem("run_failed", error=error)
        return 1


if __name__ == "__main__":
    sys.exit(main())

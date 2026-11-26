# Score log

All numbers come from `check_dev.py` and `tools/eval_stage.py` on the practice data. They
are not the official score, and no attacker has been run yet (Phase 6).

| Date | State | Personal data visible | Canaries | Medical values kept | Link precision / recall | Raw copies | Time (practice) |
|---|---|---:|---:|---:|---:|---:|---:|
| 2026-11-26 | Phases 1-5, birth dates shifted | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | about 1 s |
| 2026-11-26 | Phases 1-5, birth dates removed | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | about 1 s |
| 2026-11-26 | Phase 6, release gate | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | 1.4 s |
| 2026-11-26 | Phase 7, privacy settings | 0 / 3230 | 0 / 4 | 4817 / 4855 | 1.00 / 1.00 | 0 | 1.5 s |

Also measured on 2026-11-26:

- Dates: all 48 patients moved by a single offset (15,123 event pairs exact).
- Records with a birth date that still reveal the birth year: 0 of 111.
- Ages 418/418, districts 82/82 and PIN prefixes 82/82 truthful.
- With every name list removed, the practice results above are unchanged, so they rest on
  context rules and the cross-file sweep. Those rules were themselves written against
  practice formats.
- Scale (practice data copied with distinct IDs, one core): 1,000 files 5 s / 124 MB,
  2,000 files 11 s / 219 MB, 8,000 files 45 s / 793 MB.
- Tests: 517 pass on macOS (Python 3.13) and in Docker (Python 3.10 and 3.12).
- The sandboxed Linux container's output is byte-identical to the macOS output for the same
  secret.

## Attack replay and utility estimate

Our own tools (`tools/attacker.py`, `tools/utility.py`), averaged over three secrets.
"Score" is 25 x privacy + 20 x utility (out of 45).

| Settings | A2/A3 | A4 | A5 | Utility | Score |
|---|---:|---:|---:|---:|---:|
| 5-year bands, nothing suppressed (Phase 6) | 0.19 | 0.57 | 0.52-0.60 | 0.978 | about 37.2 |
| widest band 15 | 0.12 | 0.26 | 0.52 | 0.966 | 39.33 |
| widest band 15 + one-patient diagnoses suppressed (current) | 0.12 | 0.26 | 0.45 | 0.956 | 39.55 |
| current, but birth dates shifted instead of removed | 0.22 | 0.87 | 0.59 | 0.965 | 34.95 |

Attack values are relative to the same attack on the raw input. The medical values no
longer kept in Phase 7 are the five one-patient diagnoses, removed on purpose.


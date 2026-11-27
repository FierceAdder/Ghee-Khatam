# What is implemented so far

Status on 27 November 2026. Detail and history are in [PROJECT_REPORT.md](PROJECT_REPORT.md),
[../SCORES.md](../SCORES.md) and [CHANGES_TO_TEST.md](CHANGES_TO_TEST.md).

## Status at a glance

| Work | State |
|---|---|
| Phases 1-15 | done, on `origin/main` |
| Phase 15.5 (all six fix families), 16.0-16.2, 20.1, 22 | done and committed, not pushed |
| Phase 16 experiments and scale check | done; the targeted attack stays at 0.57, above its 0.30 target |
| Phases 17, 18, 19, 20.2, 20.3, 21 | not started |

Current results are in [../SCORES.md](../SCORES.md) under "Phase 22". The section below
describes the system as of Phase 15; the update at the top of
[PROJECT_REPORT.md](PROJECT_REPORT.md) lists what was added since.

## 1. In the repository now (Phases 1-15)

An offline, CPU-only, LLM-free privacy firewall in Python.

**Pipeline**

| Stage | What it does |
|---|---|
| Read | Reads CSV, TSV, JSON, FHIR and text; checks encodings and size; quarantines broken or unsupported files |
| Understand layout | Types columns, JSON keys and `label : value` form lines, including by their values when the label is unknown |
| Detect | Identifiers by shape and label; names and places by headers, cue words and lists; dates and ages; a medical veto so doses, diseases and doctors' names survive |
| Learned name check | IndicBERT (MIT licence, 33.4M parameters, 8-bit, fine-tuned on invented data) finds names no rule found; it only adds detections and has an 8-minute budget |
| Learn across files | Values found in one file are searched for in all files, keys and file names |
| Link | Groups each patient's records with hard "never merge" rules; leaves a record unlinked when the evidence is not enough |
| Protect | Secret-keyed pseudonyms, one date shift per patient, age ranges (Mondrian), birth dates removed, districts instead of villages, partial PIN codes, one-patient diagnoses written as `R69` |
| Check and publish | Self-scan and repair, structure check, quarantine on failure; publishes only after the checks; deterministic for a given secret |
| Log | Closed-schema run log with no input text, chained by keyed hashes (`tools/verify_log.py`) |

**Development tools:** a test-data generator with separate train and test ingredients, altered and
scrambled copies of the practice data, a rule-family ablation, an attack replay, a utility
estimate, settings tuning, a pipeline walkthrough and model training scripts.

**Documents:** README, brief (2 pages), project report, walkthrough, trap catalogue, guide for
outside test records, score log and change register.

**Measured at the end of Phase 15**

| Measure | Result |
|---|---|
| Practice data | 0 of 3,230 pieces of personal data visible, 0 of 4 canaries, 99.0% of medical values kept, linking precision and recall 1.00 |
| Unseen generator test data (seeds 4-6) | 1.2-1.9% leaked; names leaked 1 / 0 / 0; linking recall 0.67-0.79 |
| Docker, 2 CPUs | practice data 26.6 s and 334 MB; 2,000 files (copies) 30.2 s and 497 MB |
| Tests | 765 pass |

An independent hand-written test set, run once on this version, leaked 21 of 204 pieces (15 of 32
hospital numbers). Those failures are what Phase 15.5 addresses. That set now counts as tuned.

# Secret Wards: privacy firewall (team Ghee-Khatam)

An offline, CPU-only, LLM-free firewall that cleans a folder of hospital records in English,
Hindi and Hinglish. It removes personal data, keeps medical values exactly, gives each
patient one keyed pseudonym across files, and quarantines whatever it cannot clean.

- **Few dependencies.** Python 3.10+; the rules use the standard library only. Encryption
  needs `cryptography` (required: without it the firewall refuses to start). The learned
  name check needs onnxruntime, sentencepiece and numpy; without them the firewall runs on
  its rules alone and the log says so. All are in `requirements.txt`.
- **One small model, declared below.** No LLM, nothing generative, nothing downloaded at
  run time.
- **Deterministic.** Same input and `RAKSHA_SECRET` give byte-identical output.

## Repository layout

This folder is the repository root, as the submission contract requires. The organisers'
practice kit is **not** part of the repository: keep it beside this folder.

```
hackathon/
├── Secret-Wards-Toolkit/    the organisers' kit (read and test with it)
└── Ghee-Khatam/             this repository
    ├── raksha.toml          how the evaluator runs us
    ├── requirements.txt     onnxruntime, sentencepiece, numpy (the learned name check)
    ├── README.md
    ├── main.py
    ├── config/policy.yaml   privacy rules
    ├── wards/  resources/   the firewall and its word lists
    ├── tests/  tools/       tests and the development loop
    ├── docs/                project brief (PDF) and the register of changes to test
    └── Dockerfile  docker-compose.yml  Makefile
```

## Quick start with Docker

Docker Desktop (or any Docker engine with Compose v2) must be running. If the practice kit
is somewhere else, pass `TOOLKIT=/path/to/kit` to `make`.

```bash
export RAKSHA_SECRET="$(openssl rand -hex 32)"   # keep it out of files and shell scripts
make run                                          # practice data -> ./my_output
make run INPUT=/path/to/records OUTPUT=./release  # your own folder
make test                                         # test suite
make dev SHOW=20                                  # run, validate, self-check, determinism
```

The containers run with no network, a read-only root filesystem, a read-only input mount,
2 CPUs and 4 GB of memory, mirroring the evaluation sandbox. The secret is passed at run
time and is never part of the image.

## Without Docker

```bash
RAKSHA_SECRET=practice python3 main.py --input ../Secret-Wards-Toolkit/dev/input --output my_output
make dev-local                                    # full development loop
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make test-local PYTHON=.venv/bin/python
make check                                        # organisers' pre-submit check
```

### Encrypted input (optional)

```bash
RAKSHA_SECRET=... python3 tools/seal.py --in records/ --out sealed/       # or: make seal
RAKSHA_SECRET=... python3 main.py --sealed sealed/ --output release/      # or: make run-sealed
```

The sealed folder shows neither contents nor file names, is decrypted in memory only, and
gives the same `sanitized/` and `records.jsonl` as a plain run with the same secret.
`--encrypt-quarantine` also keeps an encrypted copy of each quarantined file in
`<output>/quarantine` (off by default); `tools/seal.py --open-quarantine` reads them back
into a folder you choose.

The output folder must not already contain a release. Exit codes: `0` release complete,
`2` could not start (bad arguments, missing secret, bad policy), `1` unexpected failure.

## Output

```
output/
  sanitized/       same folders, formats and (unless they held personal data) file names
  report.json      every input file by SHA-256, status, counts, timing, peak memory
  records.jsonl    which record belongs to which pseudonym (null when unsure)
  logs/run.jsonl   what the run did, as counts only (see "Run log" below)
```

## Architecture

[docs/PIPELINE_WALKTHROUGH.md](docs/PIPELINE_WALKTHROUGH.md) (also as PDF) follows one
invented patient through every stage below, showing the real output of each;
`python3 tools/explain.py` regenerates it from the code.

```
 input folder (read only)
        |
   [ ingest ]  type by content, decode; broken or unsupported -> quarantine
        |
   [ parse  ]  CSV / TSV / JSON / FHIR / text -> editable segments, fields typed
        |
   [ detect ]  normalised view -> IDs, names, places, dates, ages; medical veto
        |
   [ learn  ]  identifiers, ID shapes and villages from one file applied to all
        |
   [ link   ]  constraint-first clustering -> keyed pseudonym, or null when unsure
        |
   [ redact ]  pseudonyms, date shift, age bands, generalise, suppress rare
        |
   [ audit  ]  self-scan finished values, repair
        |
   [ verify ]  stage, re-read, check structure; fail -> quarantine
        |
   [ publish]  decrypt audited files, rename into place, report last
        |
 output/  sanitized/   records.jsonl   report.json
```

| Stage | Module | What it does |
|---|---|---|
| Ingest | `wards/ingest.py` | Identifies files by content; UTF-8/16, cp1252; quarantines empty, binary, XML, broken, too deep, symlinked and unknown files |
| Parse | `wards/documents.py`, `columns.py` | CSV/TSV/JSON/FHIR/text into editable segments; types columns and JSON keys by label *and*, when the label is unfamiliar, by what the values look like |
| Normalise | `wards/textview.py`, `normalize.py` | Undoes zero-width, Indic digits, homoglyphs, `\uXXXX`, entities and %-encoding with offsets back to the original; Devanagari transliteration and phonetic name keys |
| Detect | `wards/detect/` | Identifiers, phones, labelled and contextual names, addresses, ages, dates, encoded payloads; where detections overlap, the uncovered part is still removed |
| Veto | `wards/veto.py` | Keeps doctors, hospitals, disease eponyms, the ASHA role, doses |
| Learn | `wards/knowledge.py` | Hospital-number shapes and values learned in one file are removed everywhere, including file names |
| Link | `wards/link.py` | Strong IDs first, then name plus a unique second clue; refuses merges that conflict; `null` when unsure |
| Sweep | `wards/sweep.py` | Removes every known spelling of a patient's names, in either script |
| Privacy | `wards/privacy.py`, `redact.py` | Per-patient HMAC date shift, 5-year age bands, village to district, PIN prefix, typed tokens |
| Self-scan | `wards/audit.py` | Searches the finished values for every identifier, full name and address phrase seen in the input, through disguises and encodings; repairs hits |
| Verify | `wards/validate.py` | Re-reads each staged file: CSV shape, text lines, JSON structure, no new FHIR faults, no remaining hits; otherwise quarantines it |
| Publish | `wards/release.py`, `write.py` | Format-preserving writers; staging inside the output folder; atomic renames with `report.json` last |

### Privacy rules

Edit [config/policy.yaml](config/policy.yaml). Unknown keys or out-of-range values stop the
run.

| Data | Output |
|---|---|
| Patient name | pseudonym `PSN-XXXXXX` (HMAC-SHA256 keyed with `RAKSHA_SECRET`) or `[NAME]` |
| Relatives, phones, IDs, e-mail | typed tokens; numbers in JSON become `0` |
| Staff other than doctors (nurses, technicians, pharmacists, ASHA and ANM workers) | `[STAFF]`, never used to link patients |
| Dates | shifted by a per-patient offset of 1 to 180 days, in the original format |
| Age | a range shared by a group of patients with the same district and sex (Mondrian), at least as many as the privacy rule needs; patients in groups too small to split get a band of 5 to 15 years chosen from how crowded groups are; `90+` from 90 |
| Rare diagnosis | a code held by one patient becomes `R69` and its names `[CONDITION]`, in every patient record; documents about no one keep it |
| Options (off by default unless noted) | `l_diversity` widens bands until each group holds that many diagnoses; `rare_diagnosis_mode` can write the ICD-10 category or chapter instead of `R69` (measured worse); `age_partitioning: mondrian` (on) gives each group of patients its own age range; `name_matching: fuzzy` (on) joins near-identical name spellings before linking; `name_model` adds a learned check for names no rule found (`perceptron`, `crf` or `indicbert`) |
| Birth date | `[DOB]`; a FHIR `birthDate` is dropped. `dob_mode` can be `shift` or `year`, but both reveal the birth year. Under an unfamiliar header, dates spread over ten years or more are treated as birth dates |
| Village / tehsil / PIN | district / `[TEHSIL]` / 3-digit prefix |
| Medicines, doses, vitals, labs, diagnoses, doctors, wards | unchanged |

### Security

- The secret is read once, held in an object that cannot be printed or serialised, and a
  run without it is refused.
- Logs carry only counts, values from fixed lists, exception class names and digests
  (see "Run log").
- **Encryption at rest** (AES-256-GCM, `wards/crypto.py`). Files waiting for the audit in
  `output/.stage` are on disk only as ciphertext, bound to their path, under a key that
  exists only in the running process. Optional sealed input and quarantine use a data key
  per file, wrapped by a key derived from `RAKSHA_SECRET` (HKDF and scrypt). No key is
  written anywhere; keep the secret apart from the data.
- **Threat covered:** a copy of the output folder taken at any moment of a run holds only
  ciphertext and files that already passed the audit. The staging folder and half-written
  files are removed on success, failure and stop signals; after a forced kill they remain
  as ciphertext until the next run clears them.
- **Not covered:** raw values and the pseudonym map exist in process memory during a run,
  so disable swap or encrypt it (core dumps are disabled). A plain input folder is only
  read; it stays as protected as the storage it is on unless you seal it.
- No temporary files outside the output folder. Input is opened read-only.
- A failure in one file quarantines that file. A failure in a dataset-wide stage (or
  `SIGTERM`, or an out-of-memory error) removes everything staged, writes a report marked
  `"status": "failed"` and exits non-zero. A forced kill cannot be handled; because
  `report.json` is published last, the output then never looks complete.

### Run log

`logs/run.jsonl` (JSON Lines) records each stage's time and memory, linking decisions
(merges by kind of evidence, merges refused by the constraint that stopped them), privacy
exposure counts, the self-scan's repairs and every failure. `--log-level detail` adds, per
file, its status and reason and how many values of each type and from each detector were
removed. Short summary lines also go to standard output.

- **Closed schema** ([wards/logschema.py](wards/logschema.py)): every event, field and value
  is declared. A field is a number, a value from a fixed list, a short digest or an
  exception class name, so nothing read from the input can be written. Files are named by
  their position in `report.json`, never by path. Out-of-schema events are dropped and
  counted (tests refuse them).
- **Never logged**: values, file names, record locations, diagnosis codes (a rare code is
  itself identifying) and exception messages.
- **Tamper evidence**: each line carries an HMAC over the previous one, keyed with
  `RAKSHA_SECRET`; the last line seals the line count and the SHA-256 of `report.json`.
  `RAKSHA_SECRET=... python3 tools/verify_log.py <output>` checks it.
- **Same gate as the data**: the finished log is self-scanned like every released file; if
  anything identifying is found, the log is withheld and the release still goes out.
- A failed run still writes its log, ending with `"status": "failed"`.

### When a check finds personal data

| Where | What happens |
|---|---|
| Self-scan of finished values, during a run | Redacted in place and the file checked again; a file that still fails, or whose structure changed, is quarantined and listed with its reason; a failure of the whole run publishes nothing. Counts go to the log (`self_scan`). |
| Record locations naming someone | The record is left out of `records.jsonl` (`records_withheld`). |
| The log itself | The log is withheld. |
| Our attack replay or `check_dev.py`, during development | Treated as a bug: reproduced with invented data as a failing test, fixed for the whole family of cases, never for one example. |
| A release already handed over | Withdraw it; change `RAKSHA_SECRET` so old pseudonyms and date shifts cannot be linked to the new release; fix; release again; keep an incident note with counts and reason codes only. |

The log's `privacy` line also reports, without values, how many patients are alone in their
(age band, sex, district) group and how many hold a diagnosis shared by three or fewer
patients. On the practice data these are 29 and 13 of 48. Those risks are handled by
dataset-wide rules (the age-band width, rare-diagnosis suppression), never by changing
individual patients; wider settings were measured and cost more utility than they saved
(SCORES.md).

## Model declaration

As the rules require, every model the firewall uses:

| | |
|---|---|
| Model | IndicBERT (`ai4bharat/indic-bert`), an ALBERT encoder pretrained on 12 Indian languages including Hindi and English |
| Size | 33.4 million parameters (the cap is 50 million); shipped as 8-bit ONNX, 62 MB, in `resources/models/` |
| Licence | MIT |
| What we added | a two-way tagging layer, fine-tuned for 3 epochs to mark words that are part of a person's name (`tools/train_indicbert.py`) |
| Trained on | invented records from our generator's training side only (`tools/generator`); no practice or test data |
| Role | after the rules, it reads free text for names no rule found and adds detections; it never removes one, never links records, and doctors, diseases and doses stay protected by the rules |
| Run time | about 0.17 s per file on 2 CPUs, within an 8-minute budget; files beyond it get rules only |
| Turn off | `name_model: off` in `config/policy.yaml` |

Also included, but not used by default: a CRF (`resources/models/name_crf.crfsuite`, needs
python-crfsuite) and an averaged perceptron (`name_perceptron.json`) over hand-made features,
and a letter n-gram name-likeness table (`name_likeness.json`). All were trained on
generated data only.

## Measured results

Practice data, `check_dev.py` and our own tools, 27 Nov 2026. Not the official score.

| Measure | Value |
|---|---|
| Personal data still visible | 0 of 3,230 pieces (English, Hindi and Hinglish) |
| Planted canaries found in the output | 0 of 4 |
| Medical values kept | 99.1 % (five one-patient diagnoses removed on purpose) |
| Linking precision / recall / F0.5 | 1.00 / 1.00 / 1.00 |
| Attack replay, relative to raw input (10 secrets) | direct 0, record linkage 0.25, profile 0.23, targeted 0.57 |
| Time, peak memory (200 files), Docker on 2 CPUs | 12.2 s, 341 MB |
| 2,000 files, copies of the practice data (measured at Phase 15) | 30 s, 497 MB |
| Same secret twice; Docker against macOS | byte-identical output |

On data not used for tuning:

| Data | Leaked | Medical kept | Linking P / R |
|---|---|---|---|
| Held-out test seeds 7-9, run once | 1.4-1.9 %, no critical identifier, no canary | 99.1-100 % | 0.92-0.95 / 0.66-0.72, one false merge each |
| Blind set, 2,419 files, first run | structured identifiers 0; one given name that is also a word left visible | medicines, doses, BP all; diagnoses 99.0 % | 0.998 / 0.534 (1.000 / 0.547 after fixing the false merge) |

Altered copies, results on wording the first version had not seen, and the attack replay
are in [SCORES.md](SCORES.md).

## Development

```bash
make dev SHOW=20
python3 tools/eval_stage.py --output my_output --show 20
python3 tools/attacker.py --output my_output [--other release_with_another_secret]
python3 tools/utility.py --output my_output
python3 tools/tune.py age_band_max_years=10,15 rare_diagnosis_patients=0,1 --variants

python3 tools/mutate.py --out /tmp/altered --seed 1 --level hard
python3 main.py --input /tmp/altered/input --output /tmp/altered/release
python3 ../Secret-Wards-Toolkit/check_dev.py --output /tmp/altered/release \
    --truth /tmp/altered/truth/truth.json
```

`attacker.py` replays the published attacks and `utility.py` estimates the published
utility formula. Both are our own reading of the documents, not the official evaluator.

`eval_stage.py` counts leaks per identifying token, checks that each true patient's dates
moved by a single offset, that generalised fields stay truthful, and that every claimed
pseudonym appears in its record.

`mutate.py` writes an altered copy of the practice data with an exact answer key: new
names, renamed headers, keys, labels and folders, other date formats and encodings.
Levels are `easy`, `hard` and `holdout`.

`scramble.py` replaces every header, key, label and (with `--tier cues`) honorific and
relation word with made-up words, keeping names, identifiers and medical text, to measure
how much detection depends on wording it knows. `ablate.py` switches off one family of
evidence at a time and counts the leaks. Outside test records are collected as described in
[docs/TEST_RECORDS_GUIDE.md](docs/TEST_RECORDS_GUIDE.md).

`generator/build.py` writes invented datasets with exact answer keys: `--split train` is
like the practice data, `--split test` uses only held-out name regions, wordings and layouts
(docs/TRAPS.md). Fix what fails on the training side; measure on the test side.

Tests use invented names; those that need the practice kit are skipped without it. Design
changes still to be tried are in [docs/CHANGES_TO_TEST.md](docs/CHANGES_TO_TEST.md).

## Known limitations

- Rules were written while reading the practice data, and the altered copies reuse its
  document layouts. Results on really new formats will be worse than those above: the
  first run on unfamiliar wording leaked 7.4 % before the fixes it prompted, and some of
  those fixes added that copy's own words. With labels scrambled on top of new names, names
  that appear only once in free text still leak: 2 of 4 canaries survived (see SCORES.md).
- **Linking on unfamiliar layouts is the weakest part:** each of the three final test sets has
  one false merge, and recall there is 0.66 to 0.72.
- A given name that is also an ordinary word can stay visible, even in a name column.
- Names written all in capitals in free text are often missed.
- A name in free text with no label, relation, honorific or similar context, that is in no
  gazetteer and appears nowhere else in the dataset, is missed unless the learned name check
  is on. With IndicBERT on, names leaked on the held-out test side fell from 7-14 to 0-1 per
  dataset (SCORES.md); it adds about 12 s per 200 files and needs onnxruntime.
- All-lower-case names in prose are only found when another record already revealed them.
- Typing fields by their values can over-redact: capitalised phrases in an unfamiliar
  column beside identifiers may be taken for names, and a column of dates spread over ten
  years or more for birth dates.
- The targeted attack still succeeds 57 % of the time on practice data (calibrated attack
  replay, 10 secrets): few patients share a target's sex and age, so a kept diagnosis or the
  exact length of stay can single one out.
- The privacy settings were chosen on a practice set of 48 patients. The age-band rule
  adapts to larger data, but that behaviour has not been tested against a real registry.
- Birth dates are removed so they cannot undo the age bands. This gives up the utility
  credit the scorer awards for a generalised birth date.
- A birth date that is unlabelled everywhere in a patient's records is treated as an
  ordinary date and shifted.
- The self-scan can only find what detection saw somewhere: a name that was never
  detected anywhere is not in its list. It does not search for birth dates.
- XML, PDF, images, JSON Lines and legacy non-Unicode Hindi encodings are quarantined,
  not cleaned.
- Memory grows with input size. At the measured rate the 4 GB limit would be reached at
  roughly five times the 25 MB case; this has not been run.

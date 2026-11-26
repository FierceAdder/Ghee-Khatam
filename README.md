# Secret Wards: privacy firewall (team Ghee-Khatam)

An offline, CPU-only, LLM-free firewall that cleans a folder of hospital records in English,
Hindi and Hinglish. It removes personal data, keeps medical values exactly, gives each
patient one keyed pseudonym across files, and quarantines whatever it cannot clean.

- **No dependencies.** Python 3.10+ standard library only.
- **No models.** Rules, gazetteers and transliteration; nothing is trained or downloaded.
- **Deterministic.** Same input and `RAKSHA_SECRET` give byte-identical output.

## Repository layout

This folder is the repository root, as the submission contract requires. The organisers'
practice kit is **not** part of the repository: keep it beside this folder.

```
hackathon/
├── Secret-Wards-Toolkit/    the organisers' kit (read and test with it)
└── Ghee-Khatam/             this repository
    ├── raksha.toml          how the evaluator runs us
    ├── requirements.txt     empty: standard library only
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
   [ publish]  atomic renames, report last
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
| Age | band of 5 to 15 years, chosen once per release from how crowded district-and-sex groups are; `90+` from 90 |
| Rare diagnosis | a code held by one patient becomes `R69` and its names `[CONDITION]`, in every patient record; documents about no one keep it |
| Birth date | `[DOB]`; a FHIR `birthDate` is dropped. `dob_mode` can be `shift` or `year`, but both reveal the birth year. Under an unfamiliar header, dates spread over ten years or more are treated as birth dates |
| Village / tehsil / PIN | district / `[TEHSIL]` / 3-digit prefix |
| Medicines, doses, vitals, labs, diagnoses, doctors, wards | unchanged |

### Security

- The secret is read once, held in an object that cannot be printed or serialised, and a
  run without it is refused.
- Logs carry only counts, values from fixed lists, exception class names and digests
  (see "Run log").
- No temporary files outside the output folder; only redacted bytes are ever written.
  Input is opened read-only. Core dumps are disabled.
- Raw values and the pseudonym map exist only in process memory, so there is no
  intermediate file to encrypt and **no encryption at rest is implemented**. Keep input
  and output on encrypted storage, and disable swap or encrypt it.
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

## Measured results

Practice data, `check_dev.py` and our own tools, 26 Nov 2026. Not the official score.

| Measure | Value |
|---|---|
| Personal data still visible | 0 of 3,230 pieces (English, Hindi and Hinglish) |
| Planted canaries found in the output | 0 of 4 |
| Medical values kept | 99.2 % (the rest are five one-patient diagnoses, removed on purpose) |
| Linking precision / recall / F0.5 | 1.00 / 1.00 / 1.00 |
| Intervals between a patient's events | 100 % exact |
| Records still revealing a birth year | 0 of 111 |
| Time, peak memory (200 files) | 1.7 s, 67 MB |
| 2,000 files (6 MB) / 8,000 files (25 MB), copies of the practice data | 18 s, 215 MB / 64 s, 788 MB |

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

Tests use invented names; those that need the practice kit are skipped without it. Design
changes still to be tried are in [docs/CHANGES_TO_TEST.md](docs/CHANGES_TO_TEST.md).

## Known limitations

- Rules were written while reading the practice data, and the altered copies reuse its
  document layouts. Results on really new formats will be worse than those above: the
  first run on unfamiliar wording leaked 7.4 % before the fixes it prompted, and some of
  those fixes added that copy's own words. With labels scrambled on top of new names, names
  that appear only once in free text still leak: 2 of 4 canaries survived (see SCORES.md).
- A name in free text with no label, relation, honorific or similar context, that is in no
  gazetteer and appears nowhere else in the dataset, is missed.
- All-lower-case names in prose are only found when another record already revealed them.
- Typing fields by their values can over-redact: capitalised phrases in an unfamiliar
  column beside identifiers may be taken for names, and a column of dates spread over ten
  years or more for birth dates.
- The targeted attack still succeeds 45 to 53 % of the time on practice data, depending on
  the secret: diagnoses shared by two or three patients are kept for research value.
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

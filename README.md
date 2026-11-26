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
```

## Pipeline

```
ingest -> parse -> detect -> learn & sweep -> date order -> link -> redact + privacy -> write
```

| Stage | Module | What it does |
|---|---|---|
| Ingest | `wards/ingest.py` | Identifies files by content; UTF-8/16, cp1252; quarantines empty, binary, XML, broken, too deep, symlinked and unknown files |
| Parse | `wards/documents.py`, `columns.py` | CSV/TSV/JSON/FHIR/text into editable segments; types columns by header keywords *and* values |
| Normalise | `wards/textview.py`, `normalize.py` | Undoes zero-width, Indic digits, homoglyphs, `\uXXXX`, entities and %-encoding with offsets back to the original; Devanagari transliteration and phonetic name keys |
| Detect | `wards/detect/` | Identifiers, phones, labelled and contextual names, addresses, ages, dates, encoded payloads |
| Veto | `wards/veto.py` | Keeps doctors, hospitals, disease eponyms, the ASHA role, doses |
| Learn | `wards/knowledge.py` | Hospital-number shapes and values learned in one file are removed everywhere, including file names |
| Link | `wards/link.py` | Strong IDs first, then name plus a unique second clue; refuses merges that conflict; `null` when unsure |
| Sweep | `wards/sweep.py` | Removes every known spelling of a patient's names, in either script |
| Privacy | `wards/privacy.py`, `redact.py` | Per-patient HMAC date shift, 5-year age bands, village to district, PIN prefix, typed tokens |
| Write | `wards/write.py` | Format-preserving writers, report, records |

### Privacy rules

Edit [config/policy.yaml](config/policy.yaml). Unknown keys or out-of-range values stop the
run.

| Data | Output |
|---|---|
| Patient name | pseudonym `PSN-XXXXXX` (HMAC-SHA256 keyed with `RAKSHA_SECRET`) or `[NAME]` |
| Relatives, phones, IDs, e-mail | typed tokens; numbers in JSON become `0` |
| Dates | shifted by a per-patient offset of 1 to 180 days, in the original format |
| Age | 5-year band; `90+` from 90, and the birth date is removed |
| Birth date | `[DOB]`; a FHIR `birthDate` is dropped. `dob_mode` can be `shift` or `year`, but both reveal the birth year |
| Village / tehsil / PIN | district / `[TEHSIL]` / 3-digit prefix |
| Medicines, doses, vitals, labs, diagnoses, doctors, wards | unchanged |

### Security

- The secret is read once, held in an object that cannot be printed or serialised, and a
  run without it is refused.
- Logs carry only counts, reason codes, exception class names and file-hash prefixes.
- No temporary files; everything is held in memory. Input is opened read-only.
- A failure in one file quarantines that file; a failure in a dataset-wide stage exits
  non-zero, so a partial release is never reported as complete.

## Development

```bash
make dev SHOW=20
python3 tools/eval_stage.py --output my_output --show 20
```

`eval_stage.py` counts leaks per identifying token, checks that each true patient's dates
moved by a single offset, that generalised fields stay truthful, and that every claimed
pseudonym appears in its record. Tests use invented names and formats only. Record results
in [SCORES.md](SCORES.md).

## Known limitations

- Rules were written while reading the practice data, so practice results overstate how
  well the firewall will do on unseen formats.
- A name in free text with no label, relation, honorific or similar context, that is in no
  gazetteer and appears nowhere else in the dataset, is missed.
- All-lower-case names in prose are only found when another record already revealed them.
- There is no adaptive k-anonymity yet: a rare diagnosis combined with an exact stay
  length stays identifiable (planned, Phase 6).
- Birth dates are removed so they cannot undo the age bands. This gives up the utility
  credit the scorer awards for a generalised birth date.
- A birth date that is unlabelled everywhere in a patient's records is treated as an
  ordinary date and shifted.
- There is no final self-scan of the release before exit (planned, Phase 8).
- XML, PDF, images, JSON Lines and legacy non-Unicode Hindi encodings are quarantined,
  not cleaned.
- Memory grows with input size (about 220 MB for a 12 MB input, 800 MB for 47 MB).

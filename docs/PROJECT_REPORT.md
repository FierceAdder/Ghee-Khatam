# Ghee-Khatam: project report

What we built for Secret Wards, how it works, everything we tried, what we measured and
what we learned. Every number here comes from a tool in this repository (`SCORES.md` and
`docs/CHANGES_TO_TEST.md` hold the full records); none comes from the official evaluator,
which we have not run.

## Update, 27 November (Phases 15.5, 16, 20.1 and 22)

This section supersedes older figures below where they differ.

| Result | Value |
|---|---|
| Practice data | 0 of 3,230 visible; 0 canaries; medical 99.1 %; linking 1.00 / 1.00 |
| Attack replay, now calibrated to the organisers' raw numbers (10 secrets) | record linkage 0.25, profile 0.23, targeted 0.57; score 38.21 of 45 |
| Held-out test seeds 7-9, run once | 1.4-1.9 % leaked, no critical identifier; linking 0.92-0.95 / 0.66-0.72, one false merge each |
| Blind set (2,419 files), first run | structured identifiers 0; one given name left visible; linking 0.998 / 0.534; 31 s on 2 CPUs |
| Tests | 1,084 |

Added: fixes for failure families found by an outside test set (unfamiliar hospital numbers,
lab words read as names, addresses, file names, three linking cases); AES-256-GCM encryption of
staged files, with optional sealed input and quarantine; a calibrated attack replay, under which
no earlier privacy setting changed. One linking rule added in this work caused a false merge on
the blind set and was fixed. Not done: linking on unseen layouts (17), the run-time name swap
test (18), remaining detection items (19), hostile-file tests (20.2), the counted model budget
and scale tests (21). Detail: `SCORES.md`.

## 1. Summary

An offline, CPU-only privacy firewall for a folder of Indian hospital records in English,
Hindi (Devanagari) and Hinglish. It removes personal data, keeps medical content, gives each
patient one keyed pseudonym across files, applies measured privacy rules to what remains,
checks its own output before releasing anything, and quarantines what it cannot clean.

| Result | Value |
|---|---|
| Practice data (200 files): personal data visible | 0 of 3,230 pieces; 0 of 4 canaries |
| Practice data: medical values kept; linking precision / recall | 99.0 %; 1.00 / 1.00 |
| **Unseen generated data** (held-out names, wordings and layouts; 3 sets made after every choice) | **1.2-1.9 % leaked; names leaked 1 / 0 / 0; no canaries** |
| Same, without the learned name check | 2.4-2.9 % leaked; names leaked 13 / 7 / 14 |
| Our attack replay (score of 45 = 25 x privacy + 20 x utility, 10 secrets) | 39.63 |
| Run time, Docker on 2 CPUs | practice 26.6 s, 334 MB; about 0.17 s per unique file |
| Determinism | identical output for the same secret, on Linux and macOS |
| Tests | 765 |

The design rests on three ideas: **a release gate** (nothing is published until the finished
files are searched and re-read), **measured privacy** (every privacy setting was chosen by
replaying the published attacks against our own output), and **honest measurement on data we
did not tune on** (a generator with held-out ingredients, used once per decision).

## 2. The problem in one page

Hospitals merge years of records into one research database. The input is messy: CSV, TSV,
JSON and FHIR exports, discharge summaries, prescriptions, nursing notes, WhatsApp forwards;
several encodings; names in three scripts; IDs, phones and addresses in every format; PII in
file names, JSON keys, headers and base64 attachments; broken and unsupported files.

The scorer weighs six areas: detection (20 %, recall-weighted F2), identity resolution (15 %,
precision-weighted F0.5), re-identification resistance (25 %, seven attacks), clinical
utility (20 %), integrity (10 %) and efficiency (10 %), on hidden datasets including an
"unseen formats" round. The limits are 2 CPUs, 4 GB, 15 minutes, no network, no LLMs and no
pretrained model above 50 million parameters.

The traps that decide the score: a doctor's name must stay while a relative with the same
name goes; "C/O fever" is a complaint, "C/O Ramesh" a relative; "Dolo 650" and "1-0-1" are
doses; "Parkinson's disease" is no person; the same patient must be recognised across files,
scripts and spellings without merging two people who share a name.

## 3. Architecture

### 3.1 The pipeline

```
                          RAKSHA_SECRET (environment only; never written)
                                      │
 input folder (read only)             ▼
        │                    ┌─────────────────┐
        ▼                    │ Secret (HMAC)    │── pseudonyms, date shifts, file names,
┌──────────────────┐         └─────────────────┘    log chain
│ 1 INGEST          │ type from bytes, decode (UTF-8/16, cp1252), size and depth limits
│   wards/ingest.py │──────────────────────────► QUARANTINE (reason code; never copied)
└────────┬─────────┘
         ▼
┌──────────────────┐ CSV/TSV/JSON/FHIR/text → segments, each with a field type:
│ 2 PARSE           │  • header/key words (columns.py)
│   documents.py    │  • value shapes when the label is unknown (columns, JSON key paths)
│   columns.py      │  • form layouts in text, typed across the dataset (layout.py)
│   layout.py       │ records: CSV row, JSON object, whole document
└────────┬─────────┘
         ▼
┌──────────────────┐ on a normalised view (textview.py: zero-width, Indic digits,
│ 3 DETECT          │  homoglyphs, accents, escapes, entities, %-encoding; offsets kept)
│   detect/*.py     │  • identifiers (ids.py)  • names by cue (names.py)
│   veto.py         │  • addresses (address.py) • dates, ages (demographics.py)
│                   │  • base64 / hex payloads   • medical veto keeps doctors, diseases,
└────────┬─────────┘    doses, ASHA role, 1-0-1
         ▼
┌──────────────────┐ identifiers, ID shapes, villages learned in one file are searched for
│ 4 LEARN & SWEEP   │ in every file, key and file name (knowledge.py); the auditor
│   knowledge.py    │ remembers every identifier and full name for the release gate
└────────┬─────────┘
         ▼
┌──────────────────┐ IndicBERT (33.4M, fine-tuned, 8-bit ONNX) reads free text for names
│ 5 LEARNED CHECK   │ no rule found; only adds detections; batched, cached, 8-minute
│   namemodel.py    │ budget; never used for linking or searched for elsewhere
└────────┬─────────┘
         ▼
┌──────────────────┐ union-find, precision first: strong IDs, then name + one more clue
│ 6 LINK            │ that fits one person; refused if it would join two Aadhaar numbers,
│   link.py         │ sexes, incompatible ages or given names; near-identical spellings
│                   │ joined (Jaro-Winkler); null when unsure → HMAC pseudonym PSN-XXXXXX
└────────┬─────────┘
         ▼
┌──────────────────┐ one keyed date shift per patient; birth dates removed; age bands
│ 7 PRIVACY RULES   │ chosen from group density; Mondrian ranges per group of patients
│   privacy.py      │ (90+ top-coded); village → district; PIN → 3 digits; one-patient
│   clinical.py     │ diagnoses → R69 in all patient records; option: l-diversity
└────────┬─────────┘
         ▼
┌──────────────────┐ typed tokens, pseudonyms, shifted dates in their own format;
│ 8 REDACT          │ name sweep for every spelling of a patient's names, both scripts;
│   redact.py       │ unsafe file names replaced by keyed names
│   sweep.py        │
└────────┬─────────┘
         ▼
┌──────────────────┐ search the finished text, through disguises and encodings, for every
│ 9 AUDIT           │ identifier, full name and address phrase seen anywhere; repair
│   audit.py        │
└────────┬─────────┘
         ▼
┌──────────────────┐ write to output/.stage, read back, re-parse, compare structure;
│ 10 VERIFY         │ any leak or structural change → quarantine that file
│   validate.py     │
└────────┬─────────┘
         ▼
┌──────────────────┐ atomic renames; report.json last; on any run-wide failure: nothing
│ 11 PUBLISH        │ published, report marked failed, non-zero exit
│   release.py      │
└────────┬─────────┘
         ▼
 output/ sanitized/   records.jsonl   report.json   logs/run.jsonl (counts only, HMAC-chained)
```

### 3.2 What each part decides with

| Stage | Decides with | Breaks when |
|---|---|---|
| Ingest | file bytes, never the extension | a format we do not support (it is quarantined, as required) |
| Parse | label words; value shapes across a column, key path or form label | a column of capitalised phrases that are not names (over-redaction, untested) |
| Detect | patterns, cue words, name lists, layout, veto | a name with no cue, in no list, written once |
| Learned check | the text itself (IndicBERT), after the rules | text unlike its training data; time budget on very large inputs |
| Link | identifiers, then name plus a second clue | unseen layouts that hide the second clue (recall 0.67-0.79 on unseen data) |
| Privacy | measured attack replay | the attack replay itself reads record linkage low (0.67 against 0.92 on raw input) |
| Audit, verify | everything detected anywhere | nothing it was not told about |

### 3.3 Security design

- The secret is read once into an object that cannot be printed or serialised; runs without
  it are refused. Pseudonyms, date shifts, renamed files and the log chain are all keyed with
  it, so a different secret gives unlinkable output.
- Files awaiting the audit are written only as AES-256-GCM ciphertext inside the output folder;
  sealed input and encrypted quarantine are optional (`wards/crypto.py`). Raw values and the
  pseudonym map exist only in memory. Input is opened read-only; core dumps are disabled.
- The run log has a closed schema (numbers, fixed lists, digests, exception class names);
  files are named by position, never path; every line is HMAC-chained and sealed to
  `report.json` (`tools/verify_log.py`); the log passes the same self-scan as the data.
- Failure handling: one bad file is quarantined; a run-wide failure, `SIGTERM` or an
  out-of-memory error publishes nothing, writes a failed report and exits non-zero. A real
  `SIGTERM` was sent at four points of a 2,000-file run to confirm it.

## 4. How we worked

### 4.1 The measuring tools

| Tool | What it measures |
|---|---|
| `check_dev.py` (organisers) | leaks, canaries, medical values kept, linking, against an answer key |
| `tools/eval_stage.py` | leaks per identifying token, date offsets per patient, birth years revealed, generalised fields, split and merged patients |
| `tools/attacker.py`, `tools/utility.py` | our replay of attacks A1-A7 and our estimate of the utility formula |
| `tools/tune.py` | any policy setting compared over several secrets and attacker variants |
| `tools/mutate.py` | altered copies of the practice data (new names, labels, headers, keys, formats, encodings) |
| `tools/scramble.py` | every label and cue word replaced by made-up words (measures dependence on known wording) |
| `tools/ablate.py` | one family of evidence switched off at a time |
| `tools/generator/` | invented datasets with exact answer keys; training side and held-out test side |
| `tools/explain.py` | the walkthrough: what each stage does to one invented patient |

### 4.2 Rules we held ourselves to

- **Fix families, not examples.** A failure is reproduced with invented data as a failing
  test, and the fix must cover its kind of case.
- **Test ingredients are for measurement.** The generator's test side (name regions,
  wordings, layouts) is never used to find failures; seeds used during a phase count as tuned,
  and fresh seeds give the final number.
- **Keep a change only if it helps where it should and hurts nowhere else** (practice data,
  altered copies, medical values, meaning traps).
- **Privacy settings are chosen by score** (25 x privacy + 20 x utility), checked under
  stricter and looser attacker variants, now over 10 secrets.
- **No practice identity in the repository.** A test scans every shipped file, including
  binary model files.

## 5. Everything we tried

Kept, rejected and open, by area. The register (`docs/CHANGES_TO_TEST.md`) gives the
measurement behind each line.

### 5.1 Release safety

| Change | Result | Decision |
|---|---|---|
| Stage inside the output folder, publish by rename, report last | injected failures at every step release nothing | kept |
| Self-scan of finished files with the scorer's normalisation | 18 of 18 planted disguises found; clean files unchanged | kept |
| Repair a self-scan hit instead of dropping the file | a forgotten column is repaired, file kept | kept |
| Structural check of each output | only injected faults quarantine; no false quarantines | kept |
| Process hardening (no core dumps, SIGTERM, memory reserve) | tested with real signals | kept |
| HMAC-chained run log with closed schema; log self-scan | every tamper case detected | kept |
| `PR_SET_DUMPABLE`, restrictive `umask` | breakage risk for the scorer | deferred / rejected |

### 5.2 Privacy (score of 45; higher is better)

| Change | Result | Decision |
|---|---|---|
| Birth dates removed instead of shifted | 39.55 against 34.95 (profile attack 0.26 against 0.87) | kept |
| Age-band width chosen from group density, 5 to 15 years | widest 15 gains 2.1 over 5; 20 adds only 0.09 | kept (15) |
| One-patient diagnoses → `R69` | +0.22; thresholds of 2-5 patients lose 0.1-0.9 | kept (1 patient) |
| ICD-10 category or chapter instead of `R69` | 38.97 against 39.37: the attacker treats a category as a match | rejected |
| Rare procedures generalised | no change in the targeted attack, utility lost | rejected |
| Jitter or top-code stay lengths | at most +0.18, less than the interval accuracy lost | rejected |
| Different transformation per patient | order-dependent; the pattern identifies records | rejected (not built) |
| l-diversity (2 or 3 diagnoses per group) | no change: bands already at their widest | option, off |
| Mondrian age ranges per group | 10 secrets: 39.63 against 39.46 (worst case 39.31 against 38.91); all three attacks lower | kept (on) |
| Impossible ages removed | one value in the practice data | kept |

### 5.3 Detection

| Change | Result | Decision |
|---|---|---|
| Context rules (labels, relations, honorifics, chat senders, possessives, layout cues) | the main way names are found | kept |
| Medical veto (doctors, eponyms, doses, ASHA role, 1-0-1) | meaning traps pass | kept |
| Cross-file learning and sweep (IDs, shapes, villages, names) | IDs in file names and keys caught | kept |
| Fields typed by value shape (columns, JSON keys) | reworded copy: 240 leaks to 0, linking recall 0.50 to 0.99 | kept |
| Form layouts typed across the dataset | scrambled copies 11 to 9, 10 to 7 leaks | kept |
| Staff names removed as `[STAFF]`, never linked | staff fields had leaked through two mistyped keys | kept |
| Labelled government IDs never read as Aadhaar | training-side linking recall 0.83-0.88 to 0.96-1.00 | kept |
| Clip overlapping detections; whole-character edits | unit cases | kept |
| Medical words as first names with a person cue | unit cases | kept |
| **Learned name check: perceptron, CRF, IndicBERT** | see 6.3 | **IndicBERT kept** |
| Grammar-based protection, lower-case names, unlabelled birth dates, diagnosis by code shape | not yet done | open |

### 5.4 Linking

| Change | Result | Decision |
|---|---|---|
| Constraint-first union-find (validate before merge) | no false merges on practice or altered copies | kept |
| One identifier kind for hospital and insurance numbers | held-out recall 0.95 to 0.99 | kept |
| Unrecognised partner of a name-part column | rows stop looking like two people | kept |
| A clue in a set-aside record is not unique | removed a false merge | kept |
| A known role survives a merge with an unknown one | restored recall 0.52 to 0.74 on unseen data | kept |
| Fuzzy spellings (Jaro-Winkler 0.95 on consonant skeletons) | recall up on four sets, no false merges | kept |
| Fellegi-Sunter with EM, Bloom filters | unstable on this data; no security gain inside one process | rejected (not built) |
| Ordering, abstain outcome, two-clue rule, bridge audit, larger alias table | not yet measured | open |

### 5.5 Scale

| Change | Result | Decision |
|---|---|---|
| Single pass, everything in memory | 8,000 files (25 MB) in 64 s, 788 MB without the model | current design |
| Two-pass detector | about twice the run time | rejected |
| Packed storage, two-pass apply, two CPUs | not yet measured | open |

## 6. Findings

### 6.1 Our own tests flattered us at first

The first altered copy of the practice data leaked 7.4 % (Hindi 18.2 %, Hinglish 34.2 %)
where the practice data leaked nothing. We fixed it, but several fixes simply added that
copy's own words, so the next 0 % was fitted, not measured. We said so in the brief, built
a scrambled-label test (nothing to memorise) and later a generator whose test ingredients are
never used for fixing. The honest first number on held-out wording and layouts was
7.7-8.0 % leaked; the final number, on fresh seeds, is 1.2-1.9 %.

### 6.2 Where personal data actually escapes

Removing one family of evidence at a time showed that most names are found by several
routes, so changing a label rarely leaks anything. The real gap was narrower: **a name that
appears once, in free text, with no cue, in no list.** Canaries are exactly that. With new
names and scrambled wording, 2 of 4 canaries survived; rules alone could not close it.

### 6.3 The name-model comparison

Trained only on generated training data, validated on scrambled practice copies, measured
once on unused test seeds:

| | Validation: leaked / names / canaries | Unseen test: names leaked | Medical kept | Time (practice) |
|---|---:|---:|---:|---:|
| rules only | 9 / 7 / 2 | 13 / 7 / 14 | 99.2 % | 2 s |
| perceptron (standard library) | 4 / 2 / 1 | — | 99.2 % (altered a clean protocol) | 2 s |
| CRF | 4 / 2 / 2 | — | 99.2 % | 2 s |
| **IndicBERT** | **2 / 0 / 0** | **1 / 0 / 0** | 99.0 % | 14 s (26.6 s on 2 CPUs) |

IndicBERT understands context the feature models cannot. It first over-fitted (training loss
reached 0) and tagged clinical abbreviations (HTN, DM2, TB) as names, costing 1.5 points of
medical values; never asking it about short all-capital words and words with digits fixed
that. Batching lines across files and caching repeated lines took 2,000 files from 256 s to
30 s.

### 6.4 Mistakes we made and caught

- We once stated that tests used only invented data; they held practice names. A guard test
  now scans every shipped file, binaries included, and has since caught three more slips.
- Form-layout typing let an untyped detection erase a relative's role and cut unseen-data
  linking recall from 0.74 to 0.52. We found the cause from the log's linking counters (merges
  by name plus relative fell from 17 to 0), not by reading test records.
- A score drop from 39.55 to 39.37 looked like a regression; it was the secret. Since one
  change altered the keys that seed date shifts, one secret moved the targeted attack from
  0.45 to 0.53 for the same code. Decisions now use 10 secrets.
- A model failure first quarantined whole files; an optional check now never costs a file.

### 6.5 What the privacy measurements taught us

The biggest privacy gains came from removing birth dates (they undo age bands) and from
widening bands until groups are crowded enough, not from suppressing diagnoses. Rolling rare
codes up the ICD-10 ladder does not help against an attacker who matches categories and
ranges. Group-level age ranges (Mondrian) instead of one band width for everyone help a little and
consistently: over 10 secrets the score rises by 0.17 and the worst case by 0.40.

## 7. Results in full

| Data | Leaked | Canaries | Medical kept | Link P / R |
|---|---:|---:|---:|---:|
| Practice (200 files) | 0 of 3,230 | 0 / 4 | 99.0 % | 1.00 / 1.00 |
| Altered copies, easy and hard (tuned) | 0 | 0 / 4 | 99.2 % | 1.00 / 0.99 |
| Scrambled hard copy, rules + IndicBERT (validation) | 2 of 3,230 | 0 / 4 | 99.2 % | 1.00 / 0.96 |
| **Unseen test seeds 4-6, rules + IndicBERT** | **15 / 24 / 18 (1.2-1.9 %)** | **0 / 0 / 0** | 98.5-99.6 % | 0.95-1.00 / 0.67-0.79 |

Attack replay on practice data (relative to raw input): direct exposure 0, record linkage
0.12, profile re-identification 0.26, targeted attack 0.45-0.53 depending on the secret;
pseudonym inversion and cross-release linkage 0. Estimated utility 0.956.

## 8. Limitations and next steps

- **Linking on unseen layouts** is the weakest part (recall 0.67-0.79); one unseen set had a
  false merge we have not reproduced on the training side.
- **The targeted attack** still succeeds about half the time: diagnoses shared by two or three
  patients are kept for research value.
- **The learned check's budget** covers about 2,900 unique files on 2 CPUs; beyond that the
  rules work alone. Memory grows with input (about 500 MB for 2,000 files).
- **Our generator and our fixes come from the same author.** Records written by someone else
  (`docs/TEST_RECORDS_GUIDE.md`) remain the most important missing test.
- **Open items:** diagnoses recognised by code shape under unknown keys, lower-case names,
  unlabelled birth dates, linking refinements, compact storage.

## 9. Where to look

| Topic | File |
|---|---|
| Setup, configuration, model declaration | `README.md` |
| One patient through every stage, with real output | `docs/PIPELINE_WALKTHROUGH.md` |
| Every confusable case, as remove/keep pairs | `docs/TRAPS.md` |
| Every change tried and its measurement | `docs/CHANGES_TO_TEST.md` |
| Every measured number, by phase | `SCORES.md` |
| The two-page brief | `docs/brief.pdf` |

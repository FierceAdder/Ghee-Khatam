# Score log

All numbers come from `check_dev.py`, `tools/eval_stage.py`, `tools/attacker.py` and
`tools/utility.py`. They are not the official score: the attack replay and utility
estimate are our own reading of the published documents.

## Practice data

| Date | State | Personal data visible | Canaries | Medical values kept | Link precision / recall | Raw copies | Time |
|---|---|---:|---:|---:|---:|---:|---:|
| 2026-11-26 | Phases 1-5, birth dates shifted | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | about 1 s |
| 2026-11-26 | Phases 1-5, birth dates removed | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | about 1 s |
| 2026-11-26 | Phase 6, release gate | 0 / 3230 | 0 / 4 | 4855 / 4855 | 1.00 / 1.00 | 0 | 1.4 s |
| 2026-11-26 | Phase 7, privacy settings | 0 / 3230 | 0 / 4 | 4817 / 4855 | 1.00 / 1.00 | 0 | 1.5 s |
| 2026-11-26 | Phase 8, unfamiliar formats | 0 / 3230 | 0 / 4 | 4817 / 4855 | 1.00 / 1.00 | 0 | 1.7 s |

Also at Phase 8:

- Dates: all 48 patients moved by a single offset (15,757 event pairs exact).
- Records with a birth date that still reveal the birth year: 0 of 111.
- Ages 447/448 truthful (the other is a negative age in the data, removed), districts
  112/112, PIN prefixes 112/112.
- Scale (copies of the practice data): 2,000 files / 6 MB in 18 s with 215 MB; 8,000
  files / 25 MB in 64 s with 788 MB.
- Tests: 668 pass on macOS and in Docker. The sandboxed container's `sanitized/` and
  `records.jsonl` are byte-identical to the macOS output for the same secret.

## Altered copies (`tools/mutate.py`)

Each copy has new names, renamed headers, keys, labels and folders, other date formats and
encodings, and an exact answer key (3,230 identifying pieces). `hard` and `holdout` also
use less familiar wording and forms of address.

| Copy | Build | Personal data visible | Canaries | Medical kept | Link P / R / F0.5 | Birth year revealed |
|---|---|---:|---:|---:|---:|---:|
| hard, seed 1 | Phase 7 (first sight) | 240 (7.4 %) | 1 / 4 | 98.9 % | 0.96 / 0.50 / 0.81 | not recorded |
| easy, seeds 1-3 | Phase 8 | 0 | 0 / 4 | 99.2 % | 1.00 / 0.99 / 1.00 | 0 / 111 |
| hard, seeds 1-3 | Phase 8 | 0 | 0 / 4 | 99.2 % | 1.00 / 0.99 / 1.00 | 0 / 111 |
| **holdout, seed 1, first run** | detection fixes frozen | **0** | **0 / 4** | **99.2 %** | **1.00 / 0.95 / 0.99** | **10 / 111** |
| holdout, seeds 1-3 | after the birth-date fix | 0 | 0 / 4 | 99.2 % | 1.00 / 0.95 / 0.99 | 0 / 111 |
| holdout, seed 1 | Phase 9 linking fixes | 0 | 0 / 4 | 99.2 % | 1.00 / 0.99 / 1.00 | 0 / 111 |

Before the Phase 8 fixes, the leak rate on the hard copy was 5.9 % for Latin script,
34.2 % for Hinglish and 18.2 % for Devanagari, and linking made three false merges.

How far to trust this:

- The held-out level was written by the same author before the fixes and was not used to
  find failures until its first run, so that run is the least tuned number, not a blind test.
  It found one defect (`D.O.B.` was read as the relation "D/O"); later held-out numbers are
  tuned.
- Several Phase 8 fixes added the hard copy's own words ("spouse", "ward of", "child of",
  "care giver", "dadaji", "akka", "garu", "लाभार्थी", "बस्ती"), so the hard-copy result is
  fitted to that copy.
- The same author wrote the alterations and the fixes, and every copy reuses the practice
  data's layouts and generator. Different seeds change the names, not the wording.
- With every name list removed, the practice data and the first hard copy still showed no
  leaked piece. The scrambled-label test and the ablation below measure what that rests on.
- The Phase 9 linking fixes (one identifier kind for hospital and insurance numbers; an
  unrecognised partner of a "First Name" column read as the surname) were found on the
  held-out copy, so its later numbers are tuned.

## Scrambled labels (`tools/scramble.py`)

Every word used in a CSV header, a JSON key or a text label is replaced everywhere by a
made-up word of the same shape; `cues` also replaces honorifics, kinship words and S/o-style
relations. Names, identifiers, medical words and checked clinical text stay, so the answer
key stays exact. Phase 9 build, seed 1.

| Copy | Words replaced | Personal data visible | Canaries | Medical kept | Link P / R / F0.5 | Birth year revealed |
|---|---:|---:|---:|---:|---:|---:|
| practice, labels | 138 | 3 (0.1 %) | 0 / 4 | 99.8 % | 1.00 / 0.93 / 0.99 | 1 / 111 |
| practice, labels and cues | 208 | 3 (0.1 %) | 0 / 4 | 99.8 % | 1.00 / 0.92 / 0.98 | 1 / 111 |
| hard copy (new names), labels and cues | 212 | 11 (0.3 %) | **2 / 4** | 99.4 % | 1.00 / 0.95 / 0.99 | 1 / 111 |
| held-out copy (new names), labels and cues | 215 | 10 (0.3 %) | **2 / 4** | 99.6 % | 1.00 / 0.95 / 0.99 | 1 / 111 |

What this shows:

- Leaks stay low when labels change, because identifiers are typed by their values and most
  names are reached by several routes (see the ablation).
- **New names in unfamiliar wording are the gap**: the 7 names leaked on the scrambled hard
  copy each appear once, in a short note or a Devanagari discharge summary. Two canaries
  (-3 points each officially) are of this kind.
- "Medical kept" rises because the rare-diagnosis rule recognises diagnoses partly by the key
  word "diagnosis"; with labels scrambled it suppressed fewer of the five one-patient codes.
  That is a **privacy loss**, not a gain.
- Linking recall falls to 0.92-0.95 and one birth date is no longer recognised as such.
- The scrambled copies keep the practice layouts; a real new layout may do worse.

## Ablation (`tools/ablate.py`)

Pieces leaked / names leaked / canaries found with one family of evidence switched off.
"Scrambled" is practice data with labels and cues scrambled.

| Switched off | Practice | Scrambled | Hard copy |
|---|---:|---:|---:|
| nothing | 0 / 0 / 0 | 3 / 0 / 0 | 0 / 0 / 0 |
| label cues ("Name:") | 0 / 0 / 0 | 3 / 0 / 0 | 0 / 0 / 0 |
| relation words (S/o, W/o, C/O) | 4 / 4 / 3 | 3 / 0 / 2 | 4 / 4 / 2 |
| honorifics and titles | 0 / 0 / 0 | 18 / 15 / 0 | 14 / 14 / 0 |
| grammar and layout cues | 14 / 14 / 0 | 4 / 1 / 0 | 2 / 2 / 0 |
| typed name fields | 27 / 27 / 1 | 35 / 32 / 0 | 23 / 23 / 1 |
| name lists | 0 / 0 / 0 | 13 / 8 / 2 | 0 / 0 / 0 |
| value typing | 0 / 0 / 0 | 162 / 0 / 0 | 77 / 3 / 0 |
| name sweep across files | 0 / 0 / 0 | 3 / 0 / 0 | 14 / 14 / 0 |
| identifier sweep across files | 0 / 0 / 0 | 35 / 0 / 0 | 3 / 0 / 0 |
| self-scan repair | 0 / 0 / 0 | 12 / 0 / 0 | 0 / 0 / 0 |

Each name is usually found by more than one route, so removing one family rarely matters on
its own. Canaries, which appear once and in no list, depend on relation words and the name
lists. My earlier statement that most names "came from recognised headers" was wrong as a
measure of dependence: those detections are largely duplicated by other routes.

## Generated data (`tools/generator`)

Each dataset has 40 invented patients, about 130-190 files and an answer key produced while
the files are written (checked exact: every answer is in its file and every invented name
in a file has an answer). The **training** side uses layouts and wording like the practice
data plus training ingredients; the **test** side uses only held-out name regions
(North-east, South, Adivasi, West), label wordings, village endings, relation words and
layouts the practice data does not contain (referral slips, MCH cards, medico-legal
registers, two-row-header registers, handover notes, space-aligned lists, indexes keyed by
name, pharmacy registers, duty rosters). 82 % of the test side's labels, headers and keys
and 40 % of its running words do not occur in the practice data. Its files are shorter than
practice files (about 60 words against 230).

| Build | Split | Personal data visible | Canaries | Medical kept | Link P / R / F0.5 |
|---|---|---:|---:|---:|---:|
| Phase 12 (first run) | train, seed 1 | 2 (0.2 %) | 0 / 4 | 99.4 % | 1.00 / 0.95 / 0.99 |
| Phase 12 (first run) | **test, seeds 1-2** | **91-99 (7.7-8.0 %)** | 0 / 4 | 99.1 % | 1.00-0.94 / 0.73-0.74 |
| Phase 13 (training-side fixes) | train, seeds 1-3 | 4-7 (0.3-0.5 %) | 0 / 4 | 99.1-99.7 % | 1.00 / 0.96-1.00 |
| Phase 13 (training-side fixes) | test, seeds 1-3 | 40-63 (3.4-5.2 %) | 0-1 / 4 | 99.1 % | 0.94-1.00 / 0.73-0.88 |

Phase 13 fixes were found on the training side only: a 12-digit ration card number read as
an Aadhaar number made registration rows look like two people, and a first name plus a
relative's first name was taken as unique although set-aside records showed otherwise (one
false merge). **Caveat:** the label list added for government IDs covers the whole catalogue
family X-09, including the medico-legal and vehicle numbers used on the test side, and was
written after seeing that the test side missed all of them; the drop in other-ID leaks on the
test side (62 of 62 to 11-14) is therefore not an independent result. The rest of the test
side's change comes from the same fixes and was not used to choose them.

On the test side, what still leaks is mostly patient names (about 4 %), villages (3-16 %),
PIN codes and landmarks (about 20-30 %), and linking recall stays low (0.73-0.88), with one
false merge on seed 2. These are the targets of the Phase 14 detection rebuild.

## Phase 14: detection rebuild and name-model comparison

**Form layouts.** Lines shaped `label : value` under unknown labels are typed by their values
across the dataset. Scrambled copies: 11 to 9 and 10 to 7 leaked pieces. It first broke
linking on the test side (recall 0.74 to 0.52) by letting an untyped layout detection erase a
relative's role; a known role now survives such merges (recall back to 0.74). That
regression was noticed on the test side, so test seeds 1-3 count as tuned from here on.

**Learned name check, four candidates.** Each adds detections only (never used for linking
or searched for elsewhere). Trained on generated training data only; validated on the
scrambled practice copies (new names and wording); measured once on unused test seeds 4-6.

| Candidate | Validation: leaked / names / canaries (scrambled hard copy) | Medical kept (practice) | Practice run time |
|---|---:|---:|---:|
| off | 9 / 7 / 2 | 99.2 % | 2 s |
| standard-library classifier | 4 / 2 / 1 | 99.2 % | 2 s |
| CRF (python-crfsuite) | 4 / 2 / 2 | 99.2 % | 2 s |
| **IndicBERT** (33.4M parameters, MIT, fine-tuned, 8-bit ONNX, 62 MB) | **2 / 0 / 0** | 99.0 % | 14 s |

The standard-library classifier also changed a clean protocol document ("Differential
diagnosis"); the CRF did not. IndicBERT first tagged clinical abbreviations (HTN, DM2, TB,
ATT) as names, costing 1.5 points of medical values; words with digits and short all-capital
words are now never sent to it.

**Independent measurement, test seeds 4-6** (generated after every choice was made):

| | Leaked | Names leaked | Canaries | Medical kept | Link P / R |
|---|---:|---:|---:|---:|---:|
| off | 29 / 31 / 32 (2.4-2.9 %) | 13 / 7 / 14 | 0 / 1 / 0 | 99.1 / 99.6 / 98.5 % | 1.00 / 0.67, 1.00 / 0.79, 0.95 / 0.71 |
| **IndicBERT** | **15 / 24 / 18 (1.2-1.9 %)** | **1 / 0 / 0** | **0 / 0 / 0** | same | same |

Cost of IndicBERT: 2,000 files (copies of the practice data, so repeated lines are cached)
take 29 s and 572 MB, against 20 s and 239 MB without it; on unique text it adds about 12 s
per 200 files on this Mac. It runs within an 8-minute budget and is skipped (and counted in
the log) for files beyond it. It needs onnxruntime, sentencepiece and numpy at run time.

**Other techniques tried** (attack replay and utility estimate, score out of 45):

| Technique | Stage | Result | Decision |
|---|---|---|---|
| ICD-10 category or chapter instead of `R69` for one-patient codes | privacy | 38.97 against 39.37 (worst case 38.42 against 38.82) | rejected: the attacker treats a category or range as still matching |
| l-diversity (2 or 3 diagnoses per district, sex and age-band group) | privacy | no change: bands are already at their widest on practice data | kept as an option, off |
| Mondrian age ranges per group of patients | privacy | 10 secrets: 39.63 against 39.46 (worst case 39.31 against 38.91) | kept, on |
| Fuzzy spellings (Jaro-Winkler 0.95 on consonant skeletons) | linking | recall 0.95 to 0.96 and 0.99 to 1.00 on four validation sets, no false merges | kept, on |

Scores vary noticeably with the secret: since Phase 9 changed the keys that seed each
patient's date shift, one secret moved the targeted attack from 0.45 to 0.53 for the same
code. Privacy decisions from here use 10 secrets.

## Phase 15: released configuration

IndicBERT on (`name_model: indicbert`), fuzzy spellings on, everything else as before.

| Measure | Value |
|---|---|
| Practice data | 0 of 3,230 visible, 0 of 4 canaries, medical 99.0 %, linking 1.00 / 1.00 |
| Same secret twice; different secret; input unchanged | identical; different; unchanged |
| Docker (Linux, 2 CPUs) against macOS, same secret | byte-identical `sanitized/` and `records.jsonl` |
| Docker, 2 CPUs: practice (200 files) | 26.6 s, 334 MB |
| Docker, 2 CPUs: 2,000 files (copies of practice) | 30.2 s, 497 MB |
| Docker, 2 CPUs: 133 unique generated files | 22.0 s, 328 MB (about 0.17 s per file) |
| Tests | 765 pass locally (model packages absent: rules only); 764 pass and 1 skipped in Docker (model on) |

The learned check has an 8-minute budget: at about 0.17 s per unique file it covers roughly
2,900 files on 2 CPUs, and files beyond it are processed by the rules alone (counted in the
log as `name_model files_skipped`).

## Attack replay and utility estimate

Averaged over three secrets. "Score" is 25 x privacy + 20 x utility (out of 45). Attack
values are relative to the same attack on the raw input.

| Settings | A2/A3 | A4 | A5 | Utility | Score |
|---|---:|---:|---:|---:|---:|
| 5-year bands, nothing suppressed (Phase 6) | 0.19 | 0.57 | 0.52-0.60 | 0.978 | about 37.2 |
| widest band 15 | 0.12 | 0.26 | 0.52 | 0.966 | 39.33 |
| widest band 15 + one-patient diagnoses suppressed (current) | 0.12 | 0.26 | 0.45 | 0.956 | 39.55 |
| current, but birth dates shifted instead of removed | 0.22 | 0.87 | 0.59 | 0.965 | 34.95 |

A single run at Phase 8 with the secret `practice` gave A5 0.53, so the targeted attack
depends noticeably on the date shifts. A1, A6 and A7 were 0 in that run. The medical
values no longer kept since Phase 7 are the five one-patient diagnoses, removed on purpose.

## Phase 15.5: fixes from an outside test set

An outside hand-written set of 24 files and 12 patients, run once on the Phase 15 code, leaked
21 of 204 pieces (hospital numbers 15 of 32), kept 94 of 106 medical values and linked with
precision 1.00 and recall 0.72. It is now tuned and has not been run again, so there is no
"after" figure for it.

The fixes (T-97 to T-107) were measured on fixed sets with the learned check on:

| Datasets | Leaked, before and after | Medical kept | Linking |
|---|---|---|---|
| Practice and its easy, hard and holdout copies | 0 of 3,230 each, unchanged | 4804-4805 to 4810 of 4855 | 1.00 / 1.00 |
| Scrambled copies (4) | 2-3 of 3,230, unchanged | +5 to +6 | precision 1.00, recall 0.92-0.96 |
| Generator training seeds 1-6 and one scrambled | 2-7 per set, unchanged | unchanged | precision 1.00, recall 0.95-0.99 |

No wrong merge and no canary on any set; the same secret gives identical output. These sets hold
few of the new failure cases, so "unchanged" shows no harm; the benefit is shown only by the new
tests (984 tests pass). Known cost: on training copies with every name in capitals, leaked names
went from 64 to 65, which also shows that all-capital names are a weak spot.

## Phase 16: calibrated attack replay

The replay used for earlier privacy decisions read raw record linkage as about 0.67; the
organisers publish 0.922. After the fix (T-111), on the raw practice input:

| Reading | Organisers | Replay |
|---|---:|---:|
| Record linkage | 0.922 | 0.965 |
| Patients re-identified | 0.979 | 0.979 |
| Targeted success | 1.000 | 1.000 |
| Targeted hits, date / condition / stay | 20 / 18 / 3 | 22 / 19 / 4 |
| Unique records (not scored) | 0.701 | 0.762 |
| Confident record re-identification (not scored) | 0.836 | 0.908 |

The definitions of the last two are not published. On reference-like settings the replay reads
higher than the organisers publish for their reference system on their own dataset (record
linkage 0.499 against 0.277, patients 0.563 against 0.167, targeted 0.665 against 0.401): it is
not weaker than theirs, but the setups differ too much to call this agreement.

**Current release, 10 secrets** (score is 25 x privacy + 20 x utility; older attack figures in
this file came from the uncalibrated replay and are not comparable):

| Settings | A2/A3 | A4 | A5 | Utility | Score | Worst secret |
|---|---:|---:|---:|---:|---:|---:|
| **current** | 0.247 | 0.234 | 0.570 | 0.953 | **38.21** | 37.88 |
| birth dates shifted | 0.792 | 0.868 | 0.700 | 0.961 | 31.11 | 30.18 |
| birth year only | 0.789 | 0.872 | 0.700 | 0.960 | 31.08 | 30.61 |
| widest age band 5 | 0.289 | 0.383 | 0.570 | 0.956 | 37.22 | 36.88 |
| widest age band 10 | 0.255 | 0.255 | 0.570 | 0.955 | 38.09 | 37.75 |
| rare diagnoses kept | 0.247 | 0.234 | 0.642 | 0.962 | 38.00 | 37.66 |
| suppress up to 2 patients | 0.247 | 0.234 | 0.570 | 0.937 | 37.90 | 37.56 |
| suppress up to 3 patients | 0.247 | 0.234 | 0.570 | 0.928 | 37.71 | 37.37 |
| one age band width for all | 0.324 | 0.255 | 0.579 | 0.956 | 37.69 | 37.33 |

No earlier decision changes. A first run had scored "birth year only" above removal because the
replay could not read a bare year; that is fixed.

**On 500 invented patients** (2,500-person register, rules only): current 44.01; birth dates
shifted 43.49; one band width 43.74; the band cap and rare-diagnosis settings tie at 44.01. Risks
there are very low (A4 0.000), so this shows no setting getting worse at scale more than it
shows which is best.

**Targeted attack: 0.570, and the target of 0.30 or less is not met.** 74 % of successes come
from a condition plus the hospital and 24 % from the length of stay plus the hospital. Only 1 to 4
patient profiles are consistent with each target's sex and age, so even a diagnosis shared by
several patients singles one out. Suppressing more diagnoses, blanking districts and ignoring the
hospital did not move it; exact length of stay is kept on purpose.

## Phase 16.0: what leaked on test seeds 4-6

Existing outputs of the Phase 15 code, counts only: 57 of 3,582 pieces.

| Type | Leaked | Total |
|---|---:|---:|
| Other IDs | 24 | 138 |
| PIN codes | 21 | 76 |
| Landmarks | 10 | 76 |
| Patient names (a partial Hinglish name) | 1 | 226 |
| Villages | 1 | 432 |

All in Latin or Hinglish text content. None is a critical identifier (phone, Aadhaar, ABHA, PAN,
hospital number, email or full patient name), so the order of work did not change.

## Phase 22: final numbers

### Reserved test seeds 7-9 (held-out ingredients, run once)

Code of commit `1915340`; the one later change (T-114) leaves these results identical.

| Seed | Files | Leaked | Names | Canaries | Medical kept | Link P / R | Wrong merges | Time, memory (Mac) |
|---|---:|---|---:|---:|---|---|---:|---|
| 7 | 137 | 25 of 1,317 (1.9 %) | 1 | 0 | 482 of 482 | 0.92 / 0.72 | 1 | 10 s, 333 MB |
| 8 | 131 | 19 of 1,186 (1.6 %) | 0 | 0 | 422 of 426 | 0.95 / 0.66 | 1 | 9 s, 298 MB |
| 9 | 133 | 17 of 1,203 (1.4 %) | 0 | 0 | 444 of 448 | 0.92 / 0.67 | 1 | 10 s, 300 MB |

No critical identifier leaked: the rest are PIN codes, landmarks, other IDs, two villages and one
partial name. **Each seed has one false merge.** The Phase 15 code, run on the same seeds for
comparison, makes the same merges with the same precision and recall, leaks more (31, 27, 22)
and keeps 482, 415 and 448 medical values (so seed 9 lost 4 since, seed 8 gained 7).

### A blind dataset (2,419 files, 300 invented patients)

Supplied by the team after Phase 16 was committed and run once before any change. It is
templated, and its answer key holds only names and clinical facts, so identifiers were taken
from its structured registration records and counted automatically; this is our scorer, not the
official one.

| Measure | First run |
|---|---|
| Time, peak memory | 30 s, 403 MB on the Mac; 31 s, 359 MB in Docker on 2 CPUs; identical output; all files within the model budget |
| Contract, determinism, input | OK, identical over two runs, unchanged |
| Aadhaar, ABHA, insurance IDs, hospital numbers, phones, PINs, villages, addresses, emails, full patient names | 0 visible |
| Other automatic hits | 8 birth-date matches (equal dates in files without a birth-date column) and 1 doctor name equal to a relative's: no real leak among them |
| **Real leak** | one given name that is also an ordinary word stays visible (about 180 value-in-file pieces) |
| Medicines, doses, blood pressures | all kept |
| Diagnoses; doctor names | 1,973 of 1,993; 194 of 200 |
| Stay length in registration records | 1,200 of 1,200 preserved |
| Linking | precision 0.998, recall 0.534, one false merge |

The false merge came from a Phase 15.5 rule (the Phase 15 code makes none, at recall 0.547).
After the fix (T-114) precision is 1.000 and recall 0.547; this second figure is tuned on this
set. The low recall has a visible cause: the set's ages do not agree with its visit years, so
linking refuses merges on age. The given-name leak is open (T-115).

### Practice data, final code

0 of 3,230 pieces visible, 0 of 4 canaries, medical values kept 4,810 of 4,855 (99.1 %), linking
1.00 / 1.00, 12.2 s and 341 MB in Docker on 2 CPUs, byte-identical with macOS; 1,084 tests pass.

## Five fixes from a second outside set (27 Nov 2026)

Separators inside hospital numbers, doctors' degrees, medicines after a dosage form, names in
relation objects and patient-name keys (T-116 to T-120), each tested with new invented data.
All 15 fixed practice, altered and training-generator sets give the same counts as before:
practice data 0 of 3,230 visible, 0 canaries, 4,810 of 4,855 medical values, linking 1.00 /
1.00. The fixes therefore show no measured gain or loss on those sets. The outside set was not
run again, and the held-out seeds and the blind set were not re-measured. 1,212 tests pass.

## Dataset run: 600 files (`dataset/`), Phase 15 configuration

Output `Ghee-Khatam/dataset-output-20261126-195718`, run 2026-11-26. Secret not recorded.
The dataset has 128 patients, 1,369 records and 600 files, including the chaos cases
(encodings, FHIR, PII in keys, file names and base64, malformed files). This is the first
run of this dataset in this log, so no number here has been tuned against it.

| Measure | Practice (Phase 15) | Dataset (600 files) |
|---|---:|---:|
| Personal data visible | 0 / 3,230 | 141.5 / 7,272 (1.9 %) |
| Patient names | 0 | 32 / 1,514 (2.1 %) |
| Relative names | 0 | 21.5 / 728 (3.0 %) |
| PIN codes | not listed | 60 / 572 (10.5 %) |
| All other identifier types | 0 | 0 |
| Canaries | 0 / 4 | 28 / 38 pieces (73.7 %) |
| Medical values kept | 99.0 % | 96.4 % (of 11,496) |
| Medication kept | not listed | 76.6 % |
| Diagnoses kept | not listed | 85.2 % |
| Linking precision / recall | 1.00 / 1.00 | 1.00 / 0.98 (30 patients split or null) |
| Single date offset per patient | all | 126 / 128; 99.6 % of 15,927 intervals exact |
| Records revealing a birth year | 0 | 0 / 547 |
| Format contract | OK | CONTRACT OK |

Attack replay (release / raw, one secret):

| Attack | Practice (Phase 15) | Dataset |
|---|---:|---:|
| A2/A3 | 0.12 | 0.09 |
| A4 | 0.26 | 0.07 |
| A5 | 0.45 | 0.09 |
| A6 | 0 | 0 |
| Utility | 0.956 | 0.898 |
| Score (out of 45) | 39.55 | 39.34 |

The attack and score columns are indicative only: the practice values average three
secrets, this is one run, and the dataset differs. The privacy term here is
1 − weighted risk (0.856).

**Problems this run shows**

1. **Canaries leak.** 28 of 38 canary pieces are visible, including `CANARY-ZX0010` in
   `discharge/summary_x_0010.txt`. Each costs −3 officially. The practice data had none.
2. **A full name in an output file name.** `discharge/summary_x_0017 Diya Bano.txt`. The
   file-name rule missed it. Official cost: −1 per real full name leaked.
3. **Patient names in billing files.** The printed sample is all "Diya …" names in
   `billing/claims_x_003`, `006`, `009`, `012`, `015`, `018`, and in discharge summaries
   `0005` and `0012`. Every third billing file is affected in the sample, so this is likely
   one layout or name pattern. The total is 32 patient and 21.5 relative names.
4. **Medication lost.** Medication kept is 76.6 %; the lost lines are in discharge summaries
   `0001`–`0004` (for example "Insulin glargine 10 units at bedtime"). This is a
   medical-value problem, not a privacy one.
5. **Linking recall** is 0.98 with 30 patients split across pseudonyms or left null, for
   example PTX0002 in `registration/batch_001_*.csv`. No false merges.
6. **Utility** fell from 0.956 to 0.898. The cause is not yet investigated.

**Measurement note.** `check_dev.py` and `eval_stage.py` count different pieces. In my
earlier run (secret `practice`, output `…194605`) `check_dev.py` reported 60 of 7,942
pieces leaked (0.8 %) and 13 of 20 canaries. The 28 of 38 canary figure above comes from
`eval_stage.py`, so the two should not be compared directly.

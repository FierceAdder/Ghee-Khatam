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

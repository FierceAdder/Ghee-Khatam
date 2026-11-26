# Changes to test

Every design change that still needs evidence before we trust it. Each entry says what to
try, how to measure it and what result would make us keep it. Nothing here is assumed to
work because it sounds right.

**Sources.** The "Proposed Architecture Changes" document (six changes), the research
review of those changes (26 Nov 2026), the toolkit's scoring and attacker documents, and
problems found while building Phases 1-5.

**Status values:** `open` (not started), `planned` (assigned to a phase), `kept`,
`rejected`, `deferred`. Update the status and add the measured numbers when a test is run.

**Rule for every test:** compare against the previous build on the practice data *and* on
altered copies of it (T-30), and keep the change only if neither gets worse in a way the
score would notice.

## Release safety (proposed change 6)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-01 | Write to a staging folder inside the output folder, publish by rename, write `report.json` last | Kill the run at each stage; inject write failures | No partial release ever looks complete; nothing staged remains after a clean failure | planned (6) |
| T-02 | Self-scan of the finished files using the scorer's normalisation (NFKC, homoglyphs, zero-width, Indic digits, accents, base64, hex, escapes) | Plant known identifiers into writer paths; measure false positives on clean documents | Every plant is caught; clean documents stay byte-identical | planned (6) |
| T-03 | Repair a self-scan hit instead of dropping the file | Count files repaired and quarantined | Fewer lost files than quarantine-only, no new leaks | planned (6) |
| T-04 | Structural check of each output (CSV shape, JSON key paths and types, text line count, FHIR references) | Compare input and output structure | Only errors we introduced cause quarantine | planned (6) |
| T-05 | Process hardening: no core dumps, PII-free failure report, memory reserve | Force `MemoryError` and `SIGTERM` | Staging removed, non-zero exit, no values printed | planned (6) |
| T-06 | `PR_SET_DUMPABLE=0` | Run under a memory watchdog that reads `/proc` | The watchdog still works. Low security gain, unknown breakage risk | deferred |
| T-07 | Restrictive `umask` on outputs | Read outputs as a different user | The scorer can still read them. Risky if it runs as another user | rejected for now |

## Re-identification protection (proposed change 5)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-10 | Our own replay of attacks A1-A7 | Compare with the published readings (passthrough: A4 99.3 %, A5 97.1 %) | Raw-input results are close to those readings | planned (7) |
| T-11 | Utility estimate that follows the published formula | Cross-check with `check_dev.py` | Moves in the same direction as the toolkit's numbers | planned (7) |
| T-12 | Roll rare diagnosis codes up the ICD-10 ladder, for every holder of the code | Attack replay plus utility estimate, for k = 2, 3, 4, 5 | Total score (25 x privacy + 20 x utility) rises | planned (7) |
| T-13 | Generalise rare procedures the same way | Same | Total rises | planned (7) |
| T-14 | Move one date by a day or two for visits that stay unique on (condition, stay length) | Same, with jitter 0, 1, 2 days | Total rises after counting lost interval accuracy | planned (7) |
| T-15 | Give a still-exposed visit its own pseudonym | Same, plus linking recall | Total rises including the identity score | open |
| T-16 | Top-code long stays | Stay-length distribution and attack replay | Total rises | open |
| T-17 | Attacker variants: rarity cut-off, confidence threshold, stay-length tolerance | Re-run T-12 to T-14 under each variant | The chosen setting is good under all of them, not only the published one | planned (7) |
| T-18 | Wider age bands or district-only location for exposed patients | Attack replay | Total rises. Expected small: A5 does not use age or location | open |
| T-19 | Birth-date handling: `remove` (current) against `shift` | Attack replay A2-A4 | `remove` stays only if the privacy gain exceeds the lost generalisation credit | planned (7) |
| T-20 | Clamp shifted dates to the data window (shift-and-truncate) and check weekday patterns | Check whether edge dates bound the offset | No offset can be bounded from edge dates | open |
| T-21 | Choose a transformation per patient (the document's method) | Not to be built | Rejected: order-dependent, and the pattern of changes identifies records | rejected |

## Unseen formats and detection (proposed change 4)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-30 | Altered copies of the practice data with a matching answer key: renamed headers and keys, new names, new date and ID formats, new labels, other encodings, moved files | `check_dev.py` on each copy | Used as the acceptance test for detection work | planned (8) |
| T-31 | Medical words allowed inside a name when the context names a person ("Patient Parkinson Kumar") | Unit cases and altered copies | Recall rises, disease eponyms still preserved | planned (8) |
| T-32 | Clip, rather than drop, a detection that partly overlaps a stronger one | Unit cases, leak counts | No identifier fragment survives an overlap | planned (8) |
| T-33 | Preserve a span only on grammar evidence, not word-list membership alone | Meaning-trap cases | Precision holds with higher recall | open |
| T-34 | Round every edit outward to whole characters and combining marks | Devanagari cases | No broken matras | planned (8) |
| T-35 | More label-free person cues (relation grammar, "naam", chat senders) | Run with all name lists removed on altered copies | Leaks fall | planned (8) |
| T-36 | Small averaged-perceptron name scorer, additive only | Recall and precision on altered copies, with it on and off | Clear recall gain with no utility loss | deferred |
| T-37 | Remove name-list words that coincide with practice canary words | Leak counts with and without | Results unchanged | open |
| T-38 | Detect names written in lower case in prose | Altered chat notes | Gain without false positives | open |
| T-39 | Birth dates with no label anywhere in a patient's records | Altered notes | Detected without breaking visit dates | open |

## Linking (proposed changes 2 and 3)

The constraint-first union-find already exists. These refine it.

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-40 | Order candidate merges within a tier by evidence strength, then a stable key | Shuffle file order | Identical pseudonym groups | open |
| T-41 | Explicit ABSTAIN outcome, recorded and counted | Count abstentions on homonym sets | No change to precision, decisions explainable | open |
| T-42 | Require two agreeing clues to join two multi-record clusters on weak evidence | F0.5 on homonym-heavy copies | F0.5 rises | open |
| T-43 | Check that a second clue is rare enough in the data before using it | Same | Fewer false merges, recall holds | open |
| T-44 | Bridge-edge audit: cut the weakest bridge only when a cluster holds a conflict | Inject false links | Injected links are found | open |
| T-45 | Versioned Hindi-Latin normaliser: ISO-15919-like transliteration and a larger alias table | Held-out spelling pairs and a homonym set | Cross-script recall rises, homonyms stay apart | open |
| T-46 | Keyed tokens, one key domain per identifier type, for anything persisted | Inspect artefacts | No plaintext identifier is ever written | open |
| T-47 | Phonetic name plus birth year as a lookup key only, never as proof | F0.5 | No new false merges | open |
| T-48 | Fellegi-Sunter with EM, or Bloom-filter encodings | Not to be built | Rejected: unstable on this data, and one process gives no security boundary | rejected |

## Scale and memory (proposed change 1)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-50 | Profile where memory goes, on Linux | `tracemalloc` by line and peak RSS at 1k, 8k and 16k files | Baseline recorded | open |
| T-51 | Store detections as packed arrays and keyed hashes | Peak RSS against input size | At least 3x lower, output identical | open |
| T-52 | Detect once, drop the text, re-read and apply in a second pass | Time and memory at 8k files | Lower memory, time within 10 %, output identical | open |
| T-53 | Split work across the two CPUs | Time at 8k files | Faster and still byte-identical | open |
| T-54 | Run the detector twice (the document's two-pass design) | Not to be built | Rejected: roughly doubles runtime | rejected |

## Questions to settle by measurement

| ID | Question | Why it matters | Status |
|---|---|---|---|
| Q-01 | How much clinical utility does each rare-code rollup cost? | Sets how far T-12 can go | planned (7) |
| Q-02 | How large are the hidden datasets compared with the roughly 240 MB where memory would run out? | Decides how urgent T-50 to T-52 are | open (ask organisers) |
| Q-03 | Does the scorer inspect a leftover staging folder, and how does it treat a non-zero exit? | Decides failure behaviour | open (ask organisers) |
| Q-04 | How much of the drop on unseen formats can label-free cues recover? | Ranks detection work | planned (8) |
| Q-05 | How accurate is our attack replay compared with the real attacker? | Limits how far to trust privacy tuning | planned (7) |

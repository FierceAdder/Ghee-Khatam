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
| T-01 | Write to a staging folder inside the output folder, publish by rename, write `report.json` last | Kill the run at each stage; inject write failures | No partial release ever looks complete; nothing staged remains after a clean failure | kept: failures injected at link and at each of the three renames leave nothing released |
| T-02 | Self-scan of the finished files using the scorer's normalisation (NFKC, homoglyphs, zero-width, Indic digits, accents, base64, hex, escapes) | Plant known identifiers into writer paths; measure false positives on clean documents | Every plant is caught; clean documents stay byte-identical | kept: all 18 planted disguises found; clean documents unchanged; 0 repairs needed on practice data |
| T-03 | Repair a self-scan hit instead of dropping the file | Count files repaired and quarantined | Fewer lost files than quarantine-only, no new leaks | kept: a forgotten column is repaired and the file kept |
| T-04 | Structural check of each output (CSV shape, JSON key paths and types, text line count, FHIR references) | Compare input and output structure | Only errors we introduced cause quarantine | kept: injected writer faults quarantine only the affected file; 0 false quarantines on practice data and a 10x copy |
| T-05 | Process hardening: no core dumps, PII-free failure report, memory reserve | Force `MemoryError` and `SIGTERM` | Staging removed, non-zero exit, no values printed | kept: forced MemoryError and SIGTERM exceptions release nothing. A real `SIGTERM` was later sent at four points of a 2,000-file run: exit 143, a failed report and no records each time |
| T-06 | `PR_SET_DUMPABLE=0` | Run under a memory watchdog that reads `/proc` | The watchdog still works. Low security gain, unknown breakage risk | deferred |
| T-07 | Restrictive `umask` on outputs | Read outputs as a different user | The scorer can still read them. Risky if it runs as another user | rejected for now |
| T-08 | Structured run log with a closed schema, per-file counts at detail level, HMAC chain sealed to the report | Fuzz with identifying values and exception messages; leak scan of `logs/`; edit, remove and reorder lines | Nothing from the input is ever logged; every edit is detected | kept: 0 dropped events on practice data; schema refuses free text; all tamper cases detected |
| T-09 | Self-scan the log before publishing; withhold it on a hit | Plant a name in the log | Log withheld, release still published | kept |
| T-0A | Escalate when repairs are frequent (warn above a threshold) | Repairs per run on practice, scrambled and altered copies | A threshold that separates healthy from broken runs | open: practice runs need 0 repairs; files repaired are logged, no threshold chosen yet |

## Re-identification protection (proposed change 5)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-10 | Our own replay of attacks A1-A7 | Compare with the published readings (passthrough: A4 99.3 %, A5 97.1 %) | Raw-input results are close to those readings | kept: raw input A4 0.979 (organisers 0.979), A5 1.000 (0.9999), A2/A3 0.67 (0.92, still low) |
| T-11 | Utility estimate that follows the published formula | Cross-check with `check_dev.py` | Moves in the same direction as the toolkit's numbers | kept: practice release 0.956. An estimate; probe weights are unpublished |
| T-12 | Roll rare diagnosis codes up the ICD-10 ladder, for every holder of the code | Attack replay plus utility estimate, for k = 2, 3, 4, 5 | Total score (25 x privacy + 20 x utility) rises | kept at one patient as `R69`. Re-tested in Phase 14 as `rare_diagnosis_mode`: category and chapter score 38.97 against 39.37 for `R69` (worst case 38.42 against 38.82): the attacker treats a category or range as still matching |
| T-13 | Generalise rare procedures the same way | Same | Total rises | rejected: no change in the targeted attack, small utility loss |
| T-14 | Move one date by a day or two for visits that stay unique on (condition, stay length) | Same, with jitter 0, 1, 2 days | Total rises after counting lost interval accuracy | rejected: removing the stay signal entirely gains at most 0.18, less than the interval accuracy it would cost |
| T-15 | Give a still-exposed visit its own pseudonym | Same, plus linking recall | Total rises including the identity score | open |
| T-16 | Top-code long stays | Stay-length distribution and attack replay | Total rises | rejected with T-14: the same upper bound applies |
| T-17 | Attacker variants: rarity cut-off, confidence threshold, stay-length tolerance | Re-run T-12 to T-14 under each variant | The chosen setting is good under all of them, not only the published one | kept: one-patient suppression stays best under rarity 5, confidence 0.3 and stay tolerance 1 |
| T-18 | Wider age bands or district-only location for exposed patients | Attack replay | Total rises. Expected small: A5 does not use age or location | kept: widest band 15 gains 2.1 over 5 (A4 0.57 to 0.26); 20 adds 0.09. My expectation of a small effect was wrong |
| T-19 | Birth-date handling: `remove` (current) against `shift` | Attack replay A2-A4 | `remove` stays only if the privacy gain exceeds the lost generalisation credit | kept `remove`: 39.55 against 34.95 for `shift` (A4 0.26 against 0.87) |
| T-20 | Clamp shifted dates to the data window (shift-and-truncate) and check weekday patterns | Check whether edge dates bound the offset | No offset can be bounded from edge dates | open |
| T-21 | Choose a transformation per patient (the document's method) | Not to be built | Rejected: order-dependent, and the pattern of changes identifies records | rejected |
| T-22 | Choose the age-band width from the data (groups by district and sex, assumed registry density) | Practice picks 15; unit cases with large populations pick 5 | Matches the measured best on practice and keeps narrow bands on large data | kept on practice; the large-data behaviour is untested against a real registry |
| T-23 | Remove impossible ages (negative values in the data) instead of releasing them | Count them; check the utility estimate | The unique value is gone | kept: one such row in the practice data; costs one generalisation field |
| T-24 | l-diversity over diagnoses in each (district, sex, age band) group | Attack replay, 3 secrets | Score rises | no change on practice data (bands already at their widest); kept as an option, off |
| T-25 | Mondrian age ranges per group of patients instead of one band width | Attack replay, 3 then 10 secrets | Score rises under every attacker variant | kept: 10 secrets, 39.63 against 39.46 (worst case 39.31 against 38.91); all three attacks lower, utility 0.956 to 0.953 |

## Unseen formats and detection (proposed change 4)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-30 | Altered copies of the practice data with a matching answer key: renamed headers and keys, new names, new date and ID formats, new labels, other encodings, moved files | `check_dev.py` on each copy | Used as the acceptance test for detection work | kept: easy, hard and held-out levels; the first hard run leaked 240 of 3230 pieces and drove the Phase 8 fixes (SCORES.md) |
| T-31 | Medical words allowed inside a name when the context names a person ("Patient Parkinson Kumar") | Unit cases and altered copies | Recall rises, disease eponyms still preserved | kept on unit cases; the practice data has no such name, so no recall change was measured there. Medical values kept unchanged |
| T-32 | Clip, rather than drop, a detection that partly overlaps a stronger one | Unit cases, leak counts | No identifier fragment survives an overlap | kept on unit cases. Its separate effect on leak counts was not measured |
| T-33 | Preserve a span only on grammar evidence, not word-list membership alone | Meaning-trap cases | Precision holds with higher recall | open |
| T-34 | Round every edit outward to whole characters and combining marks | Devanagari cases | No broken matras | kept on unit cases. Its separate effect on leak counts was not measured |
| T-35 | More label-free person cues (relation grammar, "naam", chat senders) | Run with all name lists removed on altered copies | Leaks fall | kept: added spouse, child of, ward of, care giver and name-before-age cues. With all name lists removed, no piece leaks on practice data or the first hard copy |
| T-36 | Learned name check, additive only: standard-library perceptron, CRF and IndicBERT compared | Validation on scrambled practice copies; once on unused test seeds 4-6 | Clear recall gain with no utility loss | IndicBERT best: test-side names leaked 13/7/14 to 1/0/0, canaries 1 to 0, medical unchanged; about 12 s per 200 files. Shipping it awaits a decision (size and dependencies) |
| T-37 | Remove name-list words that coincide with practice canary words | Leak counts with and without | Results unchanged | open |
| T-38 | Detect names written in lower case in prose | Altered chat notes | Gain without false positives | open |
| T-39 | Birth dates with no label anywhere in a patient's records | Altered notes | Detected without breaking visit dates | open |
| T-3A | Type unfamiliar columns and JSON keys by what their values look like (identifiers, PIN, village, name, doctor, birth date) | Altered copies; medical values kept on practice data | Leaks and linking improve with no utility loss | kept, measured together with T-35: hard copy leaks 240 to 0 and linking recall 0.50 to 0.99; practice unchanged |
| T-3B | Test value typing against columns it should leave alone (long date histories, capitalised category phrases) | Hand-made tables from other record systems | No clinical column is redacted | open |
| T-3C | Scrambled-label test: replace every header, key, label and cue word with made-up words, keeping names, identifiers and medical text | `tools/scramble.py`, then `check_dev.py` | Used as a standing test that does not wear out | kept: practice 3 of 3230 leaked; scrambled hard copy 11, 2 of 4 canaries (SCORES.md) |
| T-3D | Ablation: switch off one family of evidence at a time | `tools/ablate.py` on practice, scrambled and hard copies | Shows what protection rests on | kept: names reach detection by several routes; canaries depend on relation words and name lists |
| T-3E | Recognise diagnoses for rare-condition suppression by value shape (ICD code beside a text), not by the key word "diagnosis" | Scrambled copies: suppressed codes and attack replay | All one-patient codes suppressed with labels scrambled | open: with labels scrambled most of the five codes were kept (Phase 14) |
| T-3F | One identifier kind for hospital and insurance numbers in linking | Held-out copy, unit case | Recall rises, no false merges | kept: held-out recall 0.95 to 0.99, precision 1.00 |
| T-3G | An unrecognised partner of a recognised name-part column is the other part | Unit cases, held-out copy | Rows stop looking like two people | kept |
| T-3H | Remove staff names (nurses, technicians, pharmacists, ASHA and ANM workers) as `[STAFF]`; keep doctors and role words; never link by them | Unit cases; practice and altered copies | Staff names gone, links and medical values unchanged | kept: practice and all copies unchanged (they name no staff); structured staff fields previously leaked |
| T-3I | Labelled government and programme IDs (ration card, voter ID, RCH, MLC, vehicle…) are other IDs, never Aadhaar | Generated training side; unit cases | Rows stop looking like two people | kept: training linking recall 0.83-0.88 to 0.96-1.00. Test-side other-ID numbers are not independent (see SCORES.md) |
| T-3J | Compositional generator with held-out ingredients (`tools/generator`) | Exact answer keys; coverage, novelty and realism reports | Used as the standing train/test benchmark | kept: test side leaks 3.4-5.2 % after Phase 13 |
| T-3K | Read form layouts ("label : value", wide gaps) and type unknown labels by their values across the dataset | Scrambled copies; practice; generated training side | Leaks fall, nothing else worse | kept: scrambled copies 11 to 9 and 10 to 7 leaked pieces; practice unchanged |
| T-3L | When overlapping name detections merge, a known role (patient, relative, staff) is never replaced by "unknown" | Linking counters in the run log | Name-plus-relative merges return | kept: restored test-side linking recall 0.52 to 0.74, which T-3K had broken (seen on the test side; see SCORES.md) |
| T-3M | Fuzzy spellings: join name skeletons with Jaro-Winkler at least 0.95 before linking | Linking on seven validation sets; homonym pairs | Recall rises, no false merges | kept: recall 0.95 to 0.96 and 0.99 to 1.00 on four sets, no false merges |

## Linking (proposed changes 2 and 3)

The constraint-first union-find already exists. These refine it.

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-40 | Order candidate merges within a tier by evidence strength, then a stable key | Shuffle file order | Identical pseudonym groups | open |
| T-41 | Explicit ABSTAIN outcome, recorded and counted | Count abstentions on homonym sets | No change to precision, decisions explainable | open |
| T-42 | Require two agreeing clues to join two multi-record clusters on weak evidence | F0.5 on homonym-heavy copies | F0.5 rises | open |
| T-43 | Check that a second clue is rare enough in the data before using it | Same | Fewer false merges, recall holds | kept in part: a clue also carried by a set-aside record is not unique (removed a false merge on the training side) |
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

## Fixes from an outside test set (Phase 15.5)

An outside hand-written set, run once on the Phase 15 code, showed these families. Every fix was
reproduced with new invented cases; that set is now tuned and was not used to measure them.
"Fixed sets" are practice, its altered and scrambled copies and generator training seeds 1-6.

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-97 | Type an unfamiliar column, key or form field as a hospital number when its values share one shape, are nearly unique per person and sit beside patient names | New remove/keep cases; fixed sets | Unknown numbers removed; claim, bill and specimen numbers kept | kept: new cases pass (18 of the family's 41 failed before); fixed sets unchanged |
| T-98 | Read the token after any "number" label as an identifier unless it is a date, dose, result or code | Same | Same | kept, as above |
| T-99 | A code repeated with one patient's name in two or more files is that patient's number; a value seen with two people never links | Same, plus shared ward and package codes | No new false merge | kept, as above |
| T-100 | Never ask the name model about codes, units, short all-capital words or lab terms; hospital job titles are never a person | New cases; fixed sets; training sets with names in capitals | Lab words kept, no new leaks | kept: medical values kept +5 to +6 on 8 sets, leaks unchanged; names in capitals leaked 64 to 65. Rejected part: not counting a model-only name as making a record personal (no measured gain, could leave dates unshifted) |
| T-101 | Read the word after a kinship word as a relative in any case, guarded against ordinary Hinglish; a nurse "sister" is staff | Name and ordinary-sentence cases; fixed sets | Relatives removed, medical and linking unchanged | kept: unit cases pass; fixed sets unchanged |
| T-102 | Address fields keep only public parts; locality parts after a house number are removed in prose | Same | No private part left, districts and states kept | kept, as above |
| T-103 | Learn the last private part of a combined address cell and search other files for it | Same | Village gone elsewhere, generic phrases not learned | kept, as above |
| T-104 | Search file names for every patient name part, any case, both scripts | Same | Names gone, ordinary file names unchanged | kept, as above |
| T-105 | Joined and split given names are one name | Merge and never-merge pairs; fixed sets | No false merge | kept: 30 of the family's 61 new cases failed before; no wrong merge on any fixed set |
| T-106 | A patient's registered phone plus a compatible name links a note; two fitting patients: abstain | Same | No false merge | kept, as above |
| T-107 | Records of one file that share a specimen or visit number, or name, age and sex, share a pseudonym and date shift; thin ties are not claimed in `records.jsonl` | Same | One shift per specimen; homonyms apart | kept, as above |

## Encryption at rest (Phase 20.1)

| ID | Change to test | How to measure | Keep if | Status |
|---|---|---|---|---|
| T-108 | Stage files only as AES-GCM ciphertext; publish by decrypting audited files and renaming, report last; clean up on every exit | Inspect the stage during the audit; tamper and swap files; stop and kill the run | No plaintext staged; leftovers only ciphertext | kept |
| T-109 | Optional sealed input with a data key per file and keyed file names | Sealed and plain runs on the same input; wrong secret; damaged folder | Identical release; nothing plain written | kept |
| T-110 | Optional encrypted copy of quarantined files, off by default | Contents and names of the folder; opening it | Only the secret holder can read it | kept, off by default |



| ID | Question | Why it matters | Status |
|---|---|---|---|
| Q-01 | How much clinical utility does each rare-code rollup cost? | Sets how far T-12 can go | answered: about 0.01 of utility for the five one-patient codes |
| Q-02 | How large are the hidden datasets compared with the roughly 240 MB where memory would run out? | Decides how urgent T-50 to T-52 are | open (ask organisers) |
| Q-03 | Does the scorer inspect a leftover staging folder, and how does it treat a non-zero exit? | Decides failure behaviour | open (ask organisers) |
| Q-04 | How much of the drop on unseen formats can label-free cues recover? | Ranks detection work | partly answered: scrambling labels and cues leaks 3 of 3230 on practice data and 11 (2 canaries) on the hard copy; the ablation gives each family's effect. Names that appear once in unfamiliar wording are the remaining gap |
| Q-05 | How accurate is our attack replay compared with the real attacker? | Limits how far to trust privacy tuning | partly answered: matches on A4 and A5 for raw input; A2/A3 reads low |

# Ghee-Khatam — Next Steps (final 2 hours)

Nov 27, 2026 · @Ashmit Kumar Verma

## Schedule

Fix five bugs found by an outside test set, in this order, then freeze and submit. Starting point: commit `c339e12`, which already prints READY TO SUBMIT. Phases 17–19 stay unimplemented; do not start them.

| Time | Task | Gain |
| --- | --- | --- |
| 0:00–0:30 | **G1** separators in identifiers | \~−6 penalty points removed; most linking misses fixed |
| 0:30–1:00 | **G3** doctor's degree replaced by a pseudonym | most visible bug in a demo |
| 1:00–1:20 | **G2** drug names after a dosage form | medical values, precision |
| 1:20–1:35 | **G5** then **G4** relation and patient-name keys | one leak, two unlinked records |
| 1:35–2:00 | **Freeze and submit** | safety margin; do not spend it |

**Rules**

1. Commit after each fix once tests pass, so every commit is submittable.
2. If a fix breaks practice numbers or determinism and the cause is not clear in 10 minutes, revert it and move on.
3. Write new invented test cases for each fix. Never use the strings from either outside test set (`SCH`, `BMH`, `Vanlalruata`, and so on), and do not run those sets again.
4. Skip everything else: Phases 17–19, the model time budget, the lower-case "dr" item, re-rendering the brief.

## The five fixes

### G1. Separators in identifiers (first, biggest)

**Cause.** Three separator patterns leave out `:` and other common marks, so an ID learned from a registration column (`XXX:26:004418`) is never found again in notes, letters or billing, and the release gate misses it too:

- `wards/knowledge.py` `_NUMBER_GAP`, used by `_known_numbers`;
- `wards/knowledge.py` `id_shape`, separator branch `[ \t/\-_.]`;
- `wards/audit.py` `_ID_GAP`.

**Change.**

1. One shared separator class (space, tab, `/ \ - _ . : # |`, parentheses; at most two characters between chunks), defined once and used in all three places.
2. Run the known-ID sweep before date/time detection, or veto a date or time match that lies inside a learned ID.

**Tests.** An invented ID such as `KGH:25:117731`, learned from a registration column, is removed from a note, a billing cell and a file name; the same with `#`, `|` and `\`. A time `10:30`, a ratio `1:1000`, a BP `140/90` and a date `12.04.2026` are never removed as IDs.

### G3. Doctor's degree replaced by a pseudonym

**Observed.** In `Dr. <given> <surname>, MD, DM (Neurology)`, `MD` became the patient's pseudonym; the patient shared the doctor's given name.

**Change.**

1. Reproduce with an invented doctor and patient sharing a given name; find the stage that writes the pseudonym (likely the name-variant sweep in `wards/sweep.py`, via `Md.` handling or a span offset after a vetoed doctor token).
2. Degrees and qualifications (`MBBS MD MS DM MCh DNB DGO DCH FRCS MRCP PhD`, `एम.डी.`) and every token in a doctor run are never replaced.
3. Release-gate invariant: a pseudonym may only replace a span typed as the patient's name; anywhere else is a self-scan failure and gets repaired.

**Tests.** Shared given name, degree list after the doctor, in English and Devanagari.

### G2. Drug names after a dosage form

**Cause.** `wards/veto.py` protects doses (`_DOSE`, `_FREQUENCY`) but not the drug word before them, so unlisted generics become `[NAME]`. "Alcohol: denies" was also redacted.

**Change.**

1. Veto: after `Tab`, `Cap`, `Inj`, `Syp`, `Susp`, `Oint`, `Cream`, `Gel`, `Drops`, `Neb`, `Inhaler`, `Sachet`, `Supp` (and long forms, with or without a dot), the next one to three words are a drug if a dose, unit, frequency or route (`mg`, `mcg`, `ml`, `mg/m2`, `IU`, `1-0-1`, `OD`, `BD`, `IV`, `SC`) follows within four tokens. Apply before the learned check too.
2. Add social-history words (alcohol, tobacco, smoking, gutka, betel) to the medical lexicon.

**Tests.** `Inj <unlisted generic> 200 mg IV` and `Tab <unlisted generic> 1 mg 1-0-1` keep the drug word; a name after `Tab` with no dose following is not protected.

### G5. Relative names inside a relation object

**Observed.** `"relation": {"type": "husband", "name": "<unknown single name>"}` leaked; known names in the same place were removed.

**Change.** In `wards/columns.py`, a `name`/`full_name`/`naam` field is `RELATIVE_NAME` whenever its container key is a relation word (`relation`, `relative`, `kin`, `next_of_kin`, `nok`, `guardian`, `attendant`, `emergency_contact`, `father`, `mother`, `husband`, `wife`, `spouse`, `son`, `daughter`) or a sibling holds a kinship value (including `पति`, `पत्नी`). No name-list check in that position.

**Tests.** Invented single-token names from regions absent from every list, under each container word.

### G4. Patient-name key under an unknown container

**Observed.** `{"crn_no": …, "patientName": …}` records inside a list called `people` got `[NAME]` instead of the pseudonym and stayed unlinked.

**Change.**

1. Reproduce with invented containers (`members`, `persons`, `records`, `admissions`); check in `wards/columns.py` whether the container word overrides the key.
2. A key whose own words say patient (`patientName`, `pt_name`, `patient_full_name`) names the patient whatever the container. Treat generic plural containers (people, persons, records, entries, admissions, visits, cases, registrations) as neutral.

**Tests.** The same record under four container names gives the same pseudonym and link.

## Freeze and submit (last 25 minutes)

- [ ] Full test suite and ruff pass
- [ ] `tools/run_dev.sh`: practice data still 0 leaks, 0 canaries, medical kept ≥ 99%, linking 1.00 / 1.00
- [ ] `python3 ../Secret-Wards-ToolKit/check_submission.py --repo . --run` prints READY TO SUBMIT
- [ ] One run with no network (`docker run --network none …`)
- [ ] Everything committed and pushed to `main`; the repo is public
- [ ] README known limitations, one short paragraph with your own numbers: linking recall 0.66–0.72 on unseen layouts with one false merge per held-out seed; targeted attack 57%; Phases 17–19 (linking, runtime swap test, open detection items) planned, not built
- [ ] Submit the repo link and `docs/brief.pdf` (not the walkthrough PDF the checker lists)

If time runs short, drop the README paragraph before any other item on this list.

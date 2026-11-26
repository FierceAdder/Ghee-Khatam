# Secret Wards — Solo Implementation Plan

The architecture guide was written for a team of four. This plan is the same system cut
down for **one person**: build in the order that earns the most points per hour, with a
working, submittable firewall at the end of every phase.

---

## 0. What changes when you are solo

| Team design | Solo decision | Why |
|---|---|---|
| Per-hospital partitions + Bloom-filter central join | **Single process.** Keep HMAC tokens for IDs (cheap); describe the full PPRL boundary in the brief as a deployment option | Bloom filters cost a day and lose 1–2 linkage points; zero scoring benefit |
| Self-trained CRF for names | **Rules + gazetteers + context triggers.** CRF only if Phase 8 has time | Labels/honorifics/relations catch most names; the variant sweep catches the rest |
| Fellegi–Sunter with EM | **Hard-rule tiers** (T1–T3) + union-find + cannot-link split | Same precision, a fraction of the code; the truth rule is itself tier-shaped |
| 2 worker processes | **One process** | Dev set is tiny; target < 10 s anyway |
| `fhir.resources`, `dateparser`, `phonenumbers`, `charset-normalizer` | **Stdlib + own small helpers.** Optional: `rapidfuzz`, `indic-transliteration` | Fewer deps = less memory, faster start, nothing to break offline |
| 4 owners, 8 packages | **~10 modules in one package** | Less plumbing, still testable per stage |

Everything else from the guide stays: detect → link → redact → generalize order, typed
tokens, per-patient HMAC date shifts, quarantine-on-doubt, self-scan, determinism.

---

## 1. Repo layout (minimal)

```
your-repo/
├── raksha.toml  requirements.txt  README.md  main.py
├── config/policy.yaml
├── resources/            # first_names_{latin,deva}.txt, surnames, honorifics,
│                         # name_variants.tsv, medical_lexicon.txt, districts.tsv
├── wards/
│   ├── ingest.py         # sniff, decode, parse → Record list; quarantine decisions
│   ├── normalize.py      # NFKC, digits, homoglyphs, translit, name canon, phonetic key
│   ├── detect.py         # regex IDs, phones, labels, relations, gazetteer, column typer
│   ├── veto.py           # medical allow-list (doses, vitals, Dr., eponyms, K/C/O)
│   ├── link.py           # inverted index, tiers, union-find, cannot-link
│   ├── redact.py         # pseudonyms, typed tokens, variant sweep, filename rename
│   ├── privacy.py        # date shift, age bands, location, rare-diagnosis k-check
│   ├── write.py          # CSV/TSV/JSON/FHIR/text writers, report.json, records.jsonl
│   └── security.py       # secret holder, safe logger, final self-scan
└── tools/
    ├── run_dev.sh        # run → validate → check_dev → determinism diff
    ├── attacker.py       # A2–A5 replay on dev/attacker
    ├── eval_stage.py     # detection P/R per type+script, linkage P/R per trap
    └── mutate.py         # make "unseen-format" copies of dev (see §4)
```

---

## 2. Phases

Hours are focused-work estimates (~60 h total). **Each phase ends with a commit that
passes `validate_output.py` and `check_submission.py --run`.** If time runs out, you
submit whatever the last green phase produced.

### Phase 1 — Skeleton that can't lose points (≈ 5 h)

Goal: kill every penalty and the cheap 10-point buckets before any clever work.

- Create the submission repo; copy `starter/`; add `tools/run_dev.sh` (runs main,
  validator, `check_dev.py`, and a second run + `diff -r` for determinism).
- `ingest.py`: sniff by content (BOM → UTF-16/UTF-8-sig; strict UTF-8; cp1252 fallback),
  JSON parse attempt regardless of extension, CSV delimiter sniff, FHIR via
  `resourceType == "Bundle"`. Guard deep JSON (iterative depth check or
  `sys.setrecursionlimit` + catch). Quarantine: empty, binary (PDF/JPG magic bytes),
  XML, truncated JSON, undecodable, too-deep.
- Per-file `try/except` → quarantine; log only `type(e).__name__` + sha256 prefix.
- `report.json` from the same tables that wrote the files; every input file listed by
  sha256; honest counts; `timing`, `resources.peak_rss_mb`.
- Input opened `rb` only; nothing written to TMPDIR/HOME; sorted iteration everywhere.

**Exit:** CONTRACT OK · raw copies = 0 · two runs byte-identical · score ≈ starter but
with zero penalties.

### Phase 2 — Structured PII (≈ 6 h)

Goal: the bulk of entities live in CSV/JSON fields.

- `normalize.py` basics: NFKC, zero-width strip, Devanagari digits → ASCII, homoglyph fold.
- **Column/key typer** by *values* (hit-rate of phone/Aadhaar/date/PIN/name-shape regex over
  the column), header only as a weak prior with a fuzzy keyword list (`name, naam, नाम,
  contact, mob, ph, addr, gram, mohalla, father, husband, guardian, s/o, uhid, mrn,
  aadhaar, abha, pan, policy, dob, जन्म`…).
- Regex detectors: Aadhaar 12 (Verhoeff = high confidence, unchecked = still redact),
  masked Aadhaar `XXXX-XXXX-1234`, ABHA 14 + `@abdm`, PAN, phones (+91, 0-prefix, 5-5
  split, Devanagari digits, masked `98XXXXXX12`), emails, MRN/UHID/IPD shapes
  (`[A-Z]{2,5}[/-]\d{2}[/-]\d{4,}` learned per file), PIN, DOB.
- Type-preserving replacement: strings → typed tokens; JSON numbers → `0`; never
  add/remove columns or keys; text line count unchanged.

**Exit:** `check_dev` PHONE/AADHAAR/ABHA/PAN/MRN leak < 5 % · medical kept ≥ 95 %.

### Phase 3 — Names in free text, 3 scripts (≈ 10 h) — *biggest detection lever*

- **Context rules first** (they generalise to unseen data): labels (`Name:`, `Pt.`,
  `Patient`, `नाम:`, `मरीज`), relations (`S/o, D/o, W/o, C/o, H/o-as-husband, पुत्र, पत्नी,
  पुत्री`) → relative role, honorific suffixes/prefixes (`ji, sahab, didi, bhai, ben, Shri,
  Smt, Kumari, श्री, श्रीमती`), age/sex tails (`Asha ji 48 Y / F`).
- **Gazetteers** from public name lists (not from `dev/`): Latin + Devanagari first names
  and surnames. Capitalised-token sequences adjacent to a gazetteer hit expand to full name.
- `veto.py` runs last and only removes detections: `Dr. X`, drug lexicon, dose regex
  (`\d+ ?mg`, `1-0-1`, `BD|TDS|OD|SOS|HS`, `x 5 days`), vitals (`BP 117/82`), eponyms
  (`Addison's`, `Parkinson's`), `K/C/O`, `H/O` (history), ward/hospital names, ASHA worker
  role words.
- Hidden channels through the same detector: filenames (rename to `file_<hmac8>.<ext>`
  + map in report), CSV headers, JSON keys, metadata strings (`source_path` — split on
  `\ / _ -`), base64/hex/`\uXXXX`/HTML-entity/%-decoded payloads (redact → re-encode, or
  replace the whole payload with a token if re-encoding breaks format).

**Exit:** name leak < 10 % in each of latin / deva / hinglish · contextual traps
(doctor, dose, k/c/o, eponym) intact · canaries 0/4.

### Phase 4 — Linking + pseudonyms (≈ 9 h)

- Per record, gather clues: canonical full name (cross-script via translit + variant
  table `Mohd/Md/Mohammad/मोहम्मद`), phonetic key, phone10, Aadhaar, ABHA, PAN,
  MRN-within-hospital, DOB, sex, age+event date → birth-year estimate, village, relative.
- Inverted index `clue → [record_ids]`; drop hub clues (degree > cutoff, e.g. desk
  phones, very common full names).
- Tiers, mirroring the truth's linkability rule:
  - **T1** same strong ID (Aadhaar/ABHA/PAN/phone non-hub/MRN in same hospital)
  - **T2** same canonical full name + same DOB
  - **T3** same canonical full name + (same relative **or** same village + birth-year ±1),
    and that combination unique in the dataset
- Union-find in sorted order → cannot-link split (two different Aadhaars, two sexes,
  impossible ages) at the weakest edge.
- Pseudonym = `PSN-` + base32(HMAC(secret, sorted strong-ID keys or min record id))[:6];
  resolve collisions deterministically. Full identity but no link → singleton PSN;
  thin mention (first name only) → `null`.
- Write the PSN **into** the record (name column / `Patient:` line / FHIR
  `Patient.identifier`) and into `records.jsonl` with the exact input `loc`.

**Exit:** precision ≥ 0.98, recall ≥ 0.7 on `check_dev` · homonym pairs never merged
(check with `eval_stage.py`).

### Phase 5 — Variant sweep + quasi-identifiers (≈ 7 h)

- **Variant sweep:** for each cluster, collect every name surface seen (both scripts,
  nicknames, relative names) and replace them in all of that cluster's records
  (word-boundary match, veto still applies). Biggest single recall gain on notes.
- Per-patient date shift: HMAC(secret, PSN) → ±1…180 days (never 0); per-record shift
  for `null` records. Parse many date formats; write back **in the original format**.
- Age → 5-year band containing the true age (age derived from DOB/event date when
  only DOB exists); 90+ top-code; DOB → shifted or year-only (pick by utility table:
  shifted ≤ 1 year scores 0.9 — but only if shift < 1 year, which ±180 guarantees).
- Location: house/street/mohalla/landmark → token; village → tehsil/district via
  `districts.tsv`; PIN → 3-digit prefix.

**Exit:** temporal intervals exact in `check_dev` preserve list · DOB/VILLAGE/PIN leak
≈ 0 · medical kept unchanged from Phase 4.

### Phase 6 — Own attacker + adaptive privacy (≈ 8 h)

- `tools/attacker.py`: load `dev/attacker/registry.csv` + `known_facts.jsonl`; replay
  A2/A3 (filter registry by sex, band, district, PIN3…), A4 (pool all records per PSN),
  A5 (match known visit + diagnosis + stay length; try global-shift recovery). Report
  re-id rate vs the same attack on raw input.
- `privacy.py` k-check on **pooled profiles** (age band, sex, district, diagnosis,
  stay length): if class < k, coarsen non-medical first (10-year band → district→state),
  then for diagnoses shared by ≤ 3 patients map to ICD-10 chapter/category on those
  records only; suppress only if still unique (count in `records_suppressed`).
- Watch the utility coupling: privacy only pays while utility ≥ 0.40 — re-run
  `check_dev` after every policy change.

**Exit:** own A4/A5 re-id clearly below the reference numbers (16.7 % / 40 %) with medical
kept ≥ 95 %.

### Phase 7 — Robustness for unseen formats (≈ 8 h) — *where the 30-point gap is*

- `tools/mutate.py` builds a perturbed copy of `dev/` **with a matching truth file**:
  rename/reorder headers (`Patient's Name`, `Contact#`, `Gram/Mohalla`), swap every
  patient name for a different public name (apply same mapping to `truth.json`
  surfaces), change date formats (`12-Apr-1977`, `1977/04/12`, Devanagari digits),
  change ID prefixes, re-encode files (UTF-16, cp1252), move files between folders.
- Run `check_dev.py --truth <mutated>` (or a fork of it) and fix whatever breaks;
  never fix by adding practice-specific strings.
- Scale test: concatenate/duplicate 10× → confirm < 60 s, < 512 MB, roughly linear.
- CI check: fail if any `dev/` patient name or ID appears under `resources/`.

**Exit:** mutated-set scores within ~5 points of dev scores · 10× set linear.

### Phase 8 — Security pass, README, brief (≈ 7 h)

- `security.py`: secret read once into an object with a redacted `__repr__`; logging
  filter rejects lines with digit runs > 6 or any mention-table value; **final self-scan**
  of `sanitized/`, `report.json`, `records.jsonl`, `logs/` with the same normalisation as
  the leak scanner → any hit quarantines that output file and updates the report.
- Third run with a different secret → confirm PSNs and shifts all differ.
- README: setup, architecture diagram (reuse the guide's), each stage, security design,
  declared models (none / CRF if built), known limitations.
- 2-page Project Brief from `brief/` template with measured numbers from `check_dev`,
  `attacker.py`, mutated set and timing.
- Stretch if hours remain: CRF name tagger, HMAC/Bloom PPRL boundary, FHIR structural
  validator for every Bundle.

**Exit:** `check_submission.py --run` → READY TO SUBMIT.

---

## 3. Schedule options

| If you have… | Do |
|---|---|
| ~24 h | Phases 1–4, slim 5 (date shift + age bands only), Phase 8 README/brief |
| ~3 days | Phases 1–6 + Phase 8 |
| a week | All phases, then iterate on Phase 7 mutations until gains flatten |

Rule of thumb: never start a phase until the previous one is committed green.

---

## 4. Daily loop

```bash
tools/run_dev.sh            # main → validate → check_dev --show 20 → rerun + diff
python tools/eval_stage.py  # P/R per type, per script, per channel; link P/R per trap
python tools/attacker.py    # re-id rate (from Phase 6)
```

Fix the largest leak row first; after every fix confirm **medical kept** did not drop.
Keep a `SCORES.md` log (date, commit, the five `check_dev` numbers) — it becomes the
brief's results table for free.

---

## 5. Risks to watch

| Risk | Guard |
|---|---|
| Over-redaction kills utility → also caps the 25-pt privacy score | veto module + token-retention metric in `eval_stage.py` |
| Wrong merges via common names / shared phones | hub cutoff, cannot-link split, `null` for thin mentions |
| Non-determinism from `set`/dict order | `sorted()` everywhere; determinism diff in every `run_dev.sh` |
| PII in exceptions/logs | never `str(e)`; logging filter; self-scan includes `logs/` |
| Tuning to practice data | `mutate.py` set is the acceptance test, not `dev/` |
| Running out of time mid-phase | each phase ends green and committed; submit last green |

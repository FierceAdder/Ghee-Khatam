# Secret Wards — Privacy Firewall Architecture & Dev Guide

Nov 26, 2026 · @Ashmit Kumar Verma

## Overview

We build an offline, CPU-only, LLM-free privacy firewall that detects every identifier first, resolves patients on an identity graph, and only then redacts and generalizes. Order matters: per-patient date shifts and name-variant sweeps need the linkage result, so redaction must come after clustering.

**Design principles (from the whiteboard, tightened)**

- **Local and offline.** No network during the run; 2 CPUs, 4 GB RAM, 15 minutes. Everything in memory, no temp files.
- **No LLM.** Rules, gazetteers, transliteration, a self-trained CRF and classical scoring only. Any model is declared in the README.
- **Privacy-first.** When unsure, redact; when linkage evidence is weak, emit `patient: null`; when a file can't be parsed, quarantine it.
- **Deterministic-first.** Exact identifiers decide before fuzzy ones. Same input and same `RAKSHA_SECRET` must give byte-identical output.
- **Decentralized** means each hospital processes its own data (ingest, normalize, detect) and sends only keyed linkage tokens to a central join. Raw identifiers never cross the hospital boundary; see "Per-hospital processing and the join boundary" under Stage 3.

**Where the 100 points are won**

| # | Criterion | Points | Stage that wins it | Main lever |
| --- | --- | --- | --- | --- |
| 1 | Privacy against attacks | 25 | Privacy layer | Adaptive generalization of non-medical quasi-identifiers, tuned against our own simulated attacker |
| 2 | Personal data detection | 20 | Detection engine + variant sweep | Union of rules, gazetteers and CRF; cluster-wide sweep of every known name variant |
| 3 | Medical data preservation | 20 | Detection veto + privacy layer | Medical allow-list veto; per-patient date shift keeps stay length exact |
| 4 | Patient identity consistency | 15 | Identity graph + linkage | Precision-first tiers, hub control, cannot-link constraints, `null` over a guess |
| 5 | Output quality and validity | 10 | Writer + report | Format-preserving writers, FHIR validation, honest counts, quarantine |
| 6 | Speed and reliability | 10 | Whole pipeline | Linear-time blocking, sorted iteration, crash isolation per file |

Penalties sit outside the 100: each leaked real ID or canary, any raw file copied out, any file silently skipped, and any change to the input.

## What the toolkit's scoring docs tell us

The organisers' reference solution scores 80–85 on normal hidden data but only 47–50 on the "chaos" unseen-format data. That 30-point gap is where strong teams separate, so every design choice below favours rules that read context over rules tuned to practice files. Source: `docs/SCORING.md`, `docs/ATTACKS.md`, `docs/DATASET.md`, `docs/SPEC.md`, `scoring.toml` in the [toolkit repo](https://github.com/Reinforce-SST/Secret-Wards-ToolKit).

| Finding from the kit | Number | Design consequence |
| --- | --- | --- |
| Baselines | reference 80–85 normal / 47–50 chaos; redact-all \~22; passthrough −19 | Detection keys on labels and value shapes, never on practice names, headers or file names |
| Privacy is coupled to utility | privacy × min(1, utility / 0.40) | Over-redaction caps the 25-point category, not just utility |
| A5 targeted attack | reference still 40% success; a condition shared by ≤ 3 profiles weighs ×8; stay length can't be hidden | Generalize or suppress the diagnosis on rare-condition records only |
| A2/A3 registry filters | phone, email, last-4 Aadhaar, masked phone, district, gender, age, birth year, tehsil, village, PIN | Remove masked phones and masked Aadhaar too; keep district, drop village and tehsil |
| A4 profile pooling | joins records through shared pseudonyms and `patient_ref`/UHID values | k must hold per *profile*, not per record: check the pooled evidence of each pseudonym |
| A6 / A7 | pseudonym must not embed digits or unkeyed hashes; pseudonyms and shifts must change with the secret | HMAC keyed on `RAKSHA_SECRET` for both pseudonyms and date shifts |
| Truth linkability rule | strong ID unique to the patient, or same name + (DOB, relative or village + age ±1) unique in the dataset | Linkage tiers mirror this rule exactly; anything else is `null` |
| Identity scoring | 0.4·F0.5 + 0.3·B³F1 + 0.1 each for language, fragmented, homonym traps; no pseudonyms at all → 0 | Precision first; homonym separation is 10% of the category |
| Detection scoring | F2; recall = half per-type, half per-script (latin, deva, hinglish) | Devanagari detection is a third of script recall; measure it separately |
| Leak scan normalisation | NFKC, homoglyphs folded, Indic digits, base64 / hex / `\uXXXX` / HTML entities / %-encoding decoded | Our detector and final self-scan decode the same payloads before matching |
| Schema preservation | CSV column and row count, JSON key paths and value **types**, text line count | Type-preserving replacement: a numeric Aadhaar stays a number; never join or split lines |
| Performance | full marks below 60 s and 512 MB peak RSS (standard profile); scaling exponent ≤ 1.15 | Blocking only, no all-pairs comparison; stream the bulk export |
| Penalties | −1 per surviving name or ID (cap −25); −3 per canary (cap −15); −5 per raw copy; −10 false success | Self-scan before exit; never copy an unparsed file |
| Practice set | 200 files, 815 records, 48 patients, 240 registry people, 4 canaries | Hidden sets are larger: test on synthetic sets 5–10× this size |

There are no patient images, but `misc/` holds a JPEG, a PDF, an HL7 XML file, a truncated JSON, a 1,500-deep JSON, an empty CSV, invalid UTF-8 and JSON behind a `.csv` extension. All are marked "either" in the answer key, so quarantining them is safe.

## Architecture

&#91;embedded content: pipeline · 8 stages, identity graph, attacker loop\]

Read top to bottom: nothing is redacted until stage 5, because pseudonyms and per-patient date shifts both depend on the linkage result. The dashed attacker loop is offline tuning on `dev/`, not part of the submitted run.

## Stage-by-stage design

Seven stages; each consumes the previous stage's in-memory tables and never touches disk except the final writer. Your whiteboard boxes map onto stages 0–5; stage 6 (privacy layer) and the identity graph are additions.

### Stage 0 — Ingest (your "consolidated data lake")

- **Sniff format by content, not extension**: BOM and byte patterns, JSON parse attempt, FHIR `resourceType`, delimiter sniffing for CSV/TSV.
- **Encodings**: try UTF-8 strict, then `charset-normalizer`, then cp1252/latin-1; legacy Hindi encodings that don't decode cleanly go to quarantine.
- **Shifted columns**: detect per cell, not per header. If a row's values match the pattern of the neighbouring column (phone in the name column), realign before detection.
- **Truncated / broken files**: salvage complete records only if the writer can still emit a valid file; otherwise quarantine with a generic reason.
- **Images and attachments**: strip all metadata; base64 attachments inside JSON are decoded and scanned like text. The JPEG, PDF and XML files in misc/ are quarantined; the answer key marks them "either".
- **Unit of work = a record**: CSV row (`row:N`), JSON path (`/visits/3`), or whole doc (text note, FHIR file). Every record gets an internal ID `(file_sha, loc)`.
- Hold everything in a single in-memory "lake" table: `record_id, hospital, file, loc, fields{}, text, lang_mix`. Never write it to disk.

### Stage 1 — Normalize (your "translation to Latin phonetics")

This is *transliteration*, not translation: we convert scripts, never meaning, and we keep the original span offsets so redaction hits the original text.

- Unicode NFC, strip zero-width characters, unify digits (Devanagari ०–९ → 0–9).
- Devanagari → ITRANS via `indic-transliteration`, then a simplifier: aa→a, ee/ii→i, oo/uu→u, w→v, sh/shh→s, final schwa dropped.
- Name canonicaliser: variant table (Mohd / Md / Mohammad / मोहम्मद → `mohammad`), honorific stripper (ji, sahab, didi, Shri, Smt, Mr, Mrs, Kumari, Bhai, Ben).
- Phonetic key: a custom Indian-name metaphone (collapse aspirates kh→k, bh→b, retroflex/dental pairs, v/b in Bengali-origin names). Store both the canonical string and the key.
- Phones → last 10 digits; IDs → digits only; dates → ISO via `dateparser` with Indian day-first order.

### Stage 2 — Detect (your "strip out attributes" — changed to *extract*, not strip)

Detection produces a **mention table**, not redacted text: `record_id, span, type, raw, normalized, confidence, source_detector`. Nothing is removed yet; linkage needs the values.

- **Structured fields**: classify each column by its *values* (pattern hit-rate, entropy, script, length) with the header only as a weak prior. Unseen headers still classify correctly.
- **IDs with checksums**: Aadhaar-like (12 digits, Verhoeff), ABHA (14 digits / `@abdm` address), PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), UHID/MRN (hospital-specific prefixes learned per file), insurance IDs. Checksum pass = high confidence.
- **Phones**: `phonenumbers` with region IN plus regex for 5-5 splits, +91, 0-prefix, and Devanagari digits.
- **Names in free text**: gazetteers (Latin + Devanagari first names, surnames, villages), relation triggers (S/o, D/o, W/o, C/o + capitalised token, पुत्र, पत्नी), honorific suffixes, and a self-trained CRF (features: script, case, gazetteer hit, ±2-token window, trigger distance).
- **Medical veto**: drug lexicon, dose patterns (`\d+ ?mg`, `BD|TDS|OD|SOS`, `1-0-1`), vitals (`BP 150/90`), eponymous diseases (Parkinson's, Down's), and `Dr.` + name → keep. The veto runs last and can only *remove* a detection, never add one.
- **Hidden places**: JSON keys, CSV headers, file names, FHIR `text.div` narrative, extensions, and attachment titles are all passed through the same detectors, after decoding base64, hex, \\uXXXX escapes, HTML entities and %-encoding exactly as the leak scanner does.
- **Combine**: union of detectors for recall, then the veto for precision. Every mention carries its detector so we can measure each one on `dev/`.

### Stage 3 — Identity graph (new)

- **Record nodes**: one per record. **Identifier nodes**: one per normalized value (phone, Aadhaar-like, ABHA, UHID, PAN, canonical full name, phonetic name key, relative-name key, address tokens, PIN).
- **Edges**: record → identifier it contains. Two records that share an identifier node become a candidate pair (this *is* blocking).
- **Edge weight = rarity**: weight = log(N / degree). A phone shared by 2 records is strong; one shared by 40 is noise.
- **Hub cutoff**: identifier nodes with degree above a threshold (tuned on `dev/`) are dropped from candidate generation — hospital desk phones, "Ramesh Kumar", big villages.
- Pure Python dicts are enough: an inverted index `identifier → [record_ids]`. Linear in the number of mentions.

#### Per-hospital processing and the join boundary

Stages 0–2 run once per hospital partition; only linkage tokens cross into the central graph. This is privacy-preserving record linkage, and it is a strong USP for the brief.

- **Partitions**: one per hospital system (Sanjeevani, Jeevan Jyoti, Arogya, DH Hardoi), assigned from the file's hospital header, ID prefix (`SNJ/`, `JJH-`, `UHID-`) or export metadata. Files with no clear hospital go to an "unattributed" partition that is processed the same way.
- **What crosses the boundary**, per record:
  - exact identifiers as `HMAC(secret, type + normalized value)`: Aadhaar, ABHA, phone, PAN, email, insurance ID;
  - names and relative names as Bloom-filter encodings of character bigrams of the canonical Latin form (cryptographic long-term keys, Schnell et al.), so fuzzy matching still works through Dice similarity;
  - coarse demographics only: sex, birth year, district, age band.
- **What stays inside the hospital**: raw text, original spans, the mention table and the name variants. The join returns a cluster ID per record; each hospital then redacts its own records (Stage 5) with its own variants.
- **Privacy layer input**: the join also receives each record's age band, district, ICD code and stay length, so the profile-level k check in Stage 6 can run centrally without raw values.
- **Bonus**: the 4 partitions run in 2 worker processes (the CPU budget), which roughly halves ingest-and-detect time. Results merge in sorted partition order so output stays byte-identical.
- **Trade-off to measure**: Bloom-filter matching is slightly less accurate than comparing plain names. Compare pairwise precision and recall on `dev/` against a plain-text join; if the drop is over 1–2 points, keep HMAC tokens for IDs but compare canonical names inside the single process and describe the boundary as a deployment option in the brief.

### Stage 4 — Tiered linkage (your right-hand column, reordered)

Your tiers are kept, with two changes: first name alone is never enough to link, and medical similarity only breaks ties between candidates that already share identity evidence.

1. **T1 — strong identifier**: same Aadhaar-like / ABHA / PAN / UHID-within-hospital → link, unless a cannot-link rule fires.
2. **T2 — full name + demographics**: same canonical full name (cross-script) + sex compatible + age consistent with visit dates (±1 year).
3. **T3 — name + context**: phonetic name match + any of: shared phone (non-hub), address overlap, matching relative ("W/o Rajan").
4. **T4 — episode** data only vetoes: the answer key counts only links backed by identity evidence, so a medical-episode match earns no recall and can only add false merges. Episode data is used solely to block a link (two admissions to different hospitals on overlapping dates).
5. **T5 — leftovers**: a record with a full name or strong ID becomes its own singleton patient; a thin mention (first name only, no anchor) gets `patient: null`.

- **Scoring**: each candidate pair gets a Fellegi–Sunter log-likelihood score (m/u estimated by EM on the data itself, sanity-checked on `dev/truth.json`). Tiers are just score bands with hard rules on top.
- **Clustering**: union-find over pairs above threshold, then **cannot-link repair** — a cluster containing two different strong IDs, two sexes, or impossible ages is split at its weakest edge. Repeat until clean.
- **Precision first**: threshold is tuned so pairwise precision on `dev/` stays above \~99%; recall is what we trade.

### Stage 5 — Pseudonymize and redact (your "PII redaction")

- **Pseudonym**: `PSN-` + first 6 base32 characters of `HMAC-SHA256(RAKSHA_SECRET, cluster_key)`, where `cluster_key` = the sorted, normalized strong identifiers of the cluster (fallback: sorted record IDs). Collisions checked and resolved deterministically.
- **Replacement tokens**: patient name → the PSN; relatives → `[RELATIVE]`; phones, IDs, addresses → typed tokens (`[PHONE]`, `[ID]`, `[ADDRESS]`). Doctors stay.
- **Variant sweep**: each cluster's full set of name variants, nicknames and relative names is matched again across all its records — this catches "Arif sahab" that no detector flagged.
- **Structured fields**: the PSN goes in the patient-name column so it appears inside the cleaned record as the contract requires; other PII columns get tokens. Columns are never added or removed. Replacements keep the JSON value type (a numeric Aadhaar becomes the number 0, not a string) and never change a text file's line count.

### Stage 6 — Privacy layer (new)

- **Per-patient date shift**: offset = HMAC(secret, PSN) mapped to ±N days (e.g. −180 to +180, never 0). The same offset applies to every date of that patient, so a 9-day stay stays 9 days. Unlinked records get a per-record offset.
- **Age**: 5-year bands that contain the true age; 90+ top-coded. DOB is removed after age is derived.
- **Location**: village / street → removed; district kept; PIN → first 3 digits or dropped.
- **Adaptive generalization**: compute the equivalence class of each patient on (age band, sex, district, shifted admission month, stay length, diagnosis). If the class is smaller than k, coarsen the *non-medical* fields first: district → state, 5-year → 10/20-year band, month → quarter. Diagnosis and stay length stay exact for common conditions. For a condition shared by 3 or fewer patients (the A5 attack weights these ×8 and pairs them with the unhideable stay length), generalize the diagnosis to its ICD-10 category on those records only, or suppress the record if it stays unique.
- **Suppression**: only as a last resort, for a record still unique after full generalization; counted in `records_suppressed`.
- **Measure it**: run our own attacker (Stage 4 of the dev loop) and report the re-identification rate in the brief.

### Stage 7 — Write and report (your "O/P")

- Format-preserving writers: same folders, names, columns and JSON shapes; FHIR re-validated with `fhir.resources`; everything UTF-8.
- File names that contain PII are renamed to `file_<hash8>.<ext>` and the mapping recorded in `report.json` by input SHA-256.
- `records.jsonl` lines sorted by file then loc; counts in `report.json` computed from the same tables, never estimated.

## Score maximisation playbook

The cheapest points are in the penalties and the 10-point buckets; the biggest swing is the 25-point privacy score, where most teams will stop at redaction.

**Privacy against attacks (25)**

- Build the attacker before tuning: a fake voter register from `dev/truth.json` (name, age, sex, village/PIN) plus "known facts" queries (visit date, stay length, rare diagnosis). Optimize the policy against its success rate.
- Date shifting defeats exact visit-date matching; stay length is preserved by rule, so it is a known quasi-identifier — compensate by coarsening location and age for patients with rare stay lengths.
- Spend generalization only where k is small. Blanket coarsening costs medical-preservation points for no privacy gain.

**Personal data detection (20)**

- Recall over precision for anything person-like, but never redact a dose, vital or doctor: the medical veto protects score 3 at the same time.
- The cluster-wide variant sweep is the single biggest recall gain on free text.
- Treat hidden places as first-class inputs: keys, headers, file names, FHIR narrative, attachment titles.

**Medical data preservation (20)**

- Never change a token the medical lexicon or dose regex matched.
- Age bands must contain the real age; date shifts must be per patient. Write a unit test for each.
- Generalize demographics before ever touching a diagnosis.

**Patient identity consistency (15)**

- A wrong merge costs more than a missed link: tune to \~99% pairwise precision on `dev/`, then claw back recall.
- `null` for thin mentions; singleton PSN for records with a full identity.
- Cross-script name matching is where most teams fail — measure it separately on `dev/`.

**Output quality (10) and speed (10)**

- Run `validate_output.py` and `check_submission.py` in CI on every commit.
- Report counts come from the same tables that produced the output, never hand-computed.
- Full speed marks need under 60 s and under 512 MB peak RSS on the standard profile, and runtime growth no worse than n^1.15. Target under 10 s and 300 MB on the dev set.
- One bad file must never crash the run: wrap each file, quarantine on exception, keep going.

**Penalties (avoid all)**

- Zero canary leaks: scan our own output, logs and report with the detectors before exit; anything found fails the file into quarantine.
- Never copy a raw file; never write into the input folder (open it read-only).

## Security, determinism and leak prevention

Assume the evaluation machine will be breached: nothing identifying may exist outside process memory, and nothing we print may contain a value we read.

- **No temp files.** The whole lake, mention table and graph live in memory. If memory ever forces spilling, intermediates are encrypted with AES-GCM using a key derived from `RAKSHA_SECRET` via HKDF and deleted at exit.
- **Keys away from data.** The secret is read from the environment once, held in a single object, never logged, never written, never placed in an exception.
- **PII-free logging.** Logs carry counts, stage timings and file SHA-256 prefixes only. A logging filter rejects any line containing digits longer than 6 or any token found in the mention table.
- **Exception hygiene.** Every per-file handler catches exceptions and logs only the exception *class* and the file hash — never `str(e)`, which can contain cell values.
- **Quarantine.** A quarantined file produces no output file at all, a `status: quarantined` entry with a generic reason, and is counted in `files_quarantined`.
- **Final self-scan.** Before exit, run the detectors over `sanitized/`, `report.json`, `records.jsonl` and logs. Any hit on a known mention value quarantines that output file and re-checks.
- **Determinism.** Sort every file list, dict iteration and candidate list; no `set` ordering, no `random` without a fixed seed, no time-dependent values in output. Run twice in CI and diff byte-for-byte; also run with a second secret and confirm pseudonyms and shifts change.
- **Input is read-only.** Open input files with `rb` only; CI checks input hashes before and after the run.

## Repo layout, dev loop and test harness

One Python package, one module per stage, so each stage can be measured on `dev/` in isolation.

```
your-repo/
├── raksha.toml
├── requirements.txt
├── README.md
├── main.py                 # CLI: --input --output, wires the stages
├── config/policy.yaml      # k target, band widths, shift range, hub cutoff, thresholds
├── resources/              # gazetteers, variant table, medical lexicon, CRF weights
├── wards/
│   ├── ingest/             # sniffers, readers per format, quarantine
│   ├── normalize/          # translit, name canon, phonetic key, phones, dates
│   ├── detect/             # rules, gazetteer, crf, column typer, medical veto
│   ├── graph/              # inverted index, weights, hub cutoff
│   ├── link/               # tiers, fellegi_sunter, clustering, cannot-link
│   ├── redact/             # pseudonyms, tokens, variant sweep
│   ├── privacy/            # date shift, age bands, location, adaptive k
│   ├── write/              # csv, json, fhir writers, report, records.jsonl
│   └── security/           # secret handling, log filter, self-scan
├── tools/
│   ├── attacker.py         # our own re-identification attack on dev/
│   ├── gen_synthetic.py    # Faker en_IN / hi_IN + Hinglish templates
│   └── eval_stage.py       # per-stage metrics against truth.json
└── tests/
```

**Dev loop (run on every change)**

1. `main.py` on `dev/input` with `RAKSHA_SECRET=practice`.
2. `validate_output.py` → must print CONTRACT OK.
3. `check_dev.py --show 20` → leaks, medical values kept, linking quality.
4. `tools/attacker.py` → re-identification rate; this is our proxy for the 25-point score.
5. `tools/eval_stage.py` → detection precision/recall per detector and per script; pairwise linkage precision/recall split into same-script and cross-script.
6. Determinism check: run twice, diff; run with a second secret, confirm differences.

**Generalization guard (no hardcoding)**

- `tools/gen_synthetic.py` makes held-out datasets with names, column headers and file layouts never seen in `dev/`. Every threshold is tuned on `dev/` and confirmed on at least one synthetic set.
- Gazetteers come from public name lists, never from `dev/` names; CI fails if any `dev/` patient name appears in `resources/`.

**Suggested split for a team of four**

| Owner | Stages | First milestone |
| --- | --- | --- |
| A | Ingest + write + report | Starter output passes CONTRACT OK on all dev formats |
| B | Normalize + detect | Detection recall per script measured on dev |
| C | Graph + link + pseudonyms | Pairwise precision ≥ 99% on dev |
| D | Privacy + security + attacker | Attacker running; first re-id rate reported |

## Review of the whiteboard and open questions

The pipeline order is right; the changes below are about what each box does, plus the two missing pieces (privacy layer and identity graph).

| Whiteboard box | Change | Why |
| --- | --- | --- |
| Translation to Latin phonetics | Transliterate, keep original offsets | Redaction must hit the original Devanagari text, not the Latin copy |
| Strip out attributes | Extract into a mention table; redact later | Linkage needs the values; stripping first throws away the evidence |
| Clustering | Identity graph + scored, constrained clustering | Gives blocking for free and stops chain merges through shared phones or common names |
| First-name based | Full canonical name + demographics; first name only with an anchor | First names collide constantly; a wrong merge costs more than a missed link |
| Tie-breaker using medical records | Used only to veto a link, never to create one | Two dengue patients are not the same person |
| Still left → unique entity | Singleton PSN only with a full identity; else `null` | Thin mentions guessed as unique patients risk wrong links |
| PII redaction → O/P | Add the privacy layer between them | Removing names alone loses most of the 25 privacy points |

**Open questions for you**

1. **"Decentralized"** — Resolved: each hospital processes its own data, then sends keyed tokens for a central join (Stage 3).
2. **Images** — resolved: no patient images. The JPEG, PDF and XML in `misc/` are robustness traps marked "either"; we quarantine them.
3. **Leftover policy** — resolved by `SCORING.md`: a record with `null` or its own singleton pseudonym creates no pairs, so neither hurts precision. Give a singleton pseudonym to records with a full identity (it earns pseudonym-consistency credit) and `null` to thin mentions.
4. **Replacement style** — resolved: typed tokens. The leak scanner matches name tokens after normalisation, so a realistic surrogate that equals another patient's name counts as a leak.
5. **Attacker model** — resolved by `ATTACKS.md`: the registry filters, the ×8 rare-condition weight and profile pooling are covered in the toolkit section above. The practice registry and known facts are in `dev/attacker/` for our own attacker.
6. **Team** — resolved: four people, split as in the repo-layout table.

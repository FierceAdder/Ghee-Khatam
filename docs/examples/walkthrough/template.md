# How the firewall works: one patient, step by step

This document follows a few invented hospital files through every stage of the firewall.
Every table and every output below was produced by running the real pipeline on them
(`python3 tools/explain.py` rebuilds this page), so it shows what the code does, not what we
hoped it would do. The secret used is a demo value, so the pseudonyms and date shifts here
are only examples.

> Edit `docs/examples/walkthrough/template.md`, not this file: this file is regenerated.

## The pipeline at a glance

```
 input folder (read only)
   │
   ├─ 1 INGEST    read each file's bytes, decide its type and encoding; unsafe → quarantine
   ├─ 2 PARSE     split into small pieces of text ("segments") and type each one
   ├─ 3 DETECT    find personal data in each segment; the medical veto protects
   │              doctors, diseases, doses
   ├─ 4 LEARN     what one file revealed (an ID, a village) is searched for in all files
   ├─ 5 LINK      group records into patients; give each a keyed pseudonym
   ├─ 6 PRIVACY   one date shift per patient, age bands, places made coarser,
   │              rare diagnoses removed
   ├─ 7 REDACT    write the replacements into each segment, rename unsafe file names
   ├─ 8 AUDIT     search the finished text for anything identifying seen anywhere; repair
   ├─ 9 VERIFY    write to a staging folder, read back, check the structure
   └─ 10 PUBLISH  rename into place; report.json last
   │
 output: sanitized/  records.jsonl  report.json  logs/run.jsonl
```

## The example

One patient, Sunita, appears in three files: the registration list, a Hinglish nursing
note (with a Devanagari line) and a lab report whose file name is her phone number. A
second patient appears only in the registration list. The note contains the traps a plain
tool falls into: a doctor who shares the relative's first name, "C/O fever" (complains of,
not care of), a dose that looks like a number ("Dolo 650") and a dosing pattern ("1-0-1").
There is also a scanned PDF the firewall cannot read.

<!-- explain:inputs -->

## 1. Ingest

Each file's type is decided from its bytes, not its extension. Anything the firewall
cannot clean safely (PDFs, images, broken or binary files) is quarantined: never copied
into the release, and listed in the report with a reason.

<!-- explain:ingest -->

## 2. Parse

Tables, JSON and text become segments. Each segment gets a field type, which tells
detection what to expect. A type comes from the column header or JSON key when that word
is known, and otherwise from what the values look like (ten-digit mobile numbers, ID-shaped
values, village-like names). Free text is typed "text" and goes through every detector.

<!-- explain:parse -->

## 3. Detect

Detectors run on a normalised view of the text, which undoes disguises (zero-width
characters, Indic digits, look-alike letters, escapes) while remembering where each
character came from. The last column says what each finding relies on. Findings that rely
on **known words** fail when a hospital words things differently; findings that rely on the
**shape of a value** or on **another file** do not. That difference is the subject of the
"What changes next" section below.

The medical veto runs last and can only remove a finding: it keeps doctors' names,
disease names, doses and the ASHA role.

After the rules, a learned check (IndicBERT, fine-tuned to tag name words) reads the free
text for names no rule found, such as a name written once with no label. It only adds
detections, is never used to link records, and is left off on this page so the page is the
same with or without its packages installed. In this example the rules already find every
name.

<!-- explain:detect -->

## 4. Learn and sweep

Once every file has been read, the identifiers, ID shapes and villages found anywhere are
searched for everywhere: in other files, in JSON keys and in file names. This is why a
phone number inside a file name is caught even though nothing labels it.

<!-- explain:learn -->

## 5. Link

Records are grouped into patients only on strong evidence: a shared identifier, or a
name plus a second clue that fits one person. A merge is refused if it would put two
Aadhaar numbers, two sexes, incompatible ages or two different given names into one
patient. When in doubt the answer is `null`, because a wrong merge harms two people's
histories. Each patient gets a pseudonym computed with HMAC-SHA256 under `RAKSHA_SECRET`,
so the same patient gets the same pseudonym in every file and a different secret gives
different pseudonyms. Names in Devanagari and Latin script share a phonetic key, so a
record that names a patient only in Devanagari can still be matched; in this example the
note joins through the hospital number in its first line.

<!-- explain:link -->

## 6. Privacy rules

Every date in a patient's records moves by the same keyed number of days, so intervals
(length of stay, days between tests) stay exact while the real dates are hidden. Birth
dates are removed, ages become bands, villages become their district, PIN codes keep
three digits, and a diagnosis held by only one patient is replaced in every patient record.

<!-- explain:privacy -->

## 7–10. Redact, audit, verify, publish

Replacements are written in place, keeping each file's format: typed tokens such as
`[PHONE]` and `[ID]`, pseudonyms for patients, shifted dates in their original format.
File names holding personal data get a keyed name:

<!-- explain:renamed -->

The finished text is then searched once
more for every identifier, full name and address phrase detected anywhere in the input,
through disguises and encodings; a hit is repaired, and a file that still fails is
quarantined. Each file is written to a staging folder, read back and checked against the
original's structure before anything is published, and `report.json` is renamed into place
last, so an interrupted run never looks complete.

The released files:

<!-- explain:outputs -->

Which record belongs to which patient (`records.jsonl`):

<!-- explain:records -->

The report (file list omitted):

<!-- explain:report -->

The run log holds only counts, values from fixed lists and digests, and every line is
chained with an HMAC so that an edited log is detected (`tools/verify_log.py`). Selected
lines, without timings, the chain fields and (except for the self-scan) zero counts:

<!-- explain:log -->

## What changes next

Most of what the firewall finds, it finds through more than one route, so changing one
label rarely leaks anything (see `SCORES.md`, "Scrambled labels" and "Ablation"). The
remaining gap is a name that appears **once**, in free text, in wording the firewall has
not seen and in no name list: with new names and scrambled wording, two of four planted
canaries got through.

The plan replaces the inside of stage 3 and leaves the other stages as they are:

```
2 PARSE      + read form layouts in text ("anything : value") without knowing the label word
3 DETECT  →  CANDIDATES   every span that could be personal (capitalised or Devanagari runs,
                           numbers, form values); nothing decided yet
             EVIDENCE     clues about each candidate, described by kind, never by the word:
                           a cue is present, the layout says it is a field value, the spelling
                           looks like a name, it sits next to a confirmed identifier
             DATASET      how many patients' records contain it; whether it appears in
             STATISTICS   documents about no patient (protocols, formularies)
             SCORE        a small trained model (a CRF tagger or an encoder under 50M
                           parameters, chosen by measurement) adds the clues together
             doctors, disease names and doses stay protected by fixed rules
```

Each change is kept only if it lowers leaks on wording we did not write ourselves without
losing medical content.

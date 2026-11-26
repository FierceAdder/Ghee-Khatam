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

**`lab/report_9812345670.json`**

```json
{
  "patient": {"name": "Sunita Kerketta", "uhid": "KMC/26/004411"},
  "collected": "13/04/2026",
  "results": [
    {"test": "Haemoglobin", "value": 10.2, "unit": "g/dL"},
    {"test": "Platelet count", "value": 150000, "unit": "/uL"}
  ],
  "referred_by": "Dr. Birsa Oraon"
}
```

**`notes/nursing_004411.txt`**

```text
Nursing note - KMC/26/004411
12/04/2026 08:00 - Sunita ji ko raat se bukhar hai, temp 101 F. C/O fever x 2 days.
Attendant: Birsa Kerketta (husband), mob 98123 45670.
Dr. Birsa Oraon reviewed. Adv: Tab Dolo 650 1-0-1 x 3 days.
सुनीता केरकेट्टा को भर्ती किया गया।
```

**`registration/patients.csv`**

```csv
UHID,Patient Name,Sex,Age,DOB,Husband/Father Name,Mobile,Aadhaar,Village,District,PIN
KMC/26/004411,Sunita Kerketta,F,47,14/03/1978,Birsa Kerketta,9812345670,4821 7365 9012,Devipur,Sitapur,261001
KMC/26/004412,Tashi Wangdi,M,62,02/01/1963,Pema Wangdi,9876501234,7391 4628 5510,Rampurwa,Sitapur,261203
```

**`scans/consent.pdf`**

```text
%PDF-1.4
% scanned consent form
```

## 1. Ingest

Each file's type is decided from its bytes, not its extension. Anything the firewall
cannot clean safely (PDFs, images, broken or binary files) is quarantined: never copied
into the release, and listed in the report with a reason.

| File | Read as | Encoding | Outcome |
|---|---|---|---|
| `lab/report_9812345670.json` | json | utf-8 | cleaned |
| `notes/nursing_004411.txt` | text | utf-8 | cleaned |
| `registration/patients.csv` | csv | utf-8 | cleaned |
| `scans/consent.pdf` | unknown | - | quarantined: unsupported_format |

## 2. Parse

Tables, JSON and text become segments. Each segment gets a field type, which tells
detection what to expect. A type comes from the column header or JSON key when that word
is known, and otherwise from what the values look like (ten-digit mobile numbers, ID-shaped
values, village-like names). Free text is typed "text" and goes through every detector.

| File | Column or key | Typed as | Decided by |
|---|---|---|---|
| `lab/report_9812345670.json` | name | patient_name | header or key word |
| `lab/report_9812345670.json` | uhid | mrn | header or key word |
| `lab/report_9812345670.json` | collected | date | header or key word |
| `lab/report_9812345670.json` | test | clinical | header or key word |
| `lab/report_9812345670.json` | value | clinical | header or key word |
| `lab/report_9812345670.json` | unit | org | header or key word |
| `lab/report_9812345670.json` | referred_by | doctor_name | header or key word |
| `notes/nursing_004411.txt` | (whole note) | text | free text |
| `registration/patients.csv` | UHID | mrn | header or key word |
| `registration/patients.csv` | Patient Name | patient_name | header or key word |
| `registration/patients.csv` | Sex | sex | header or key word |
| `registration/patients.csv` | Age | age | header or key word |
| `registration/patients.csv` | DOB | dob | header or key word |
| `registration/patients.csv` | Husband/Father Name | relative_name | header or key word |
| `registration/patients.csv` | Mobile | phone | header or key word |
| `registration/patients.csv` | Aadhaar | aadhaar | header or key word |
| `registration/patients.csv` | Village | village | header or key word |
| `registration/patients.csv` | District | district | header or key word |
| `registration/patients.csv` | PIN | pin | header or key word |

## 3. Detect

Detectors run on a normalised view of the text, which undoes disguises (zero-width
characters, Indic digits, look-alike letters, escapes) while remembering where each
character came from. The last column says what each finding relies on. Findings that rely
on **known words** fail when a hospital words things differently; findings that rely on the
**shape of a value** or on **another file** do not. That difference is the subject of the
"What changes next" section below.

The medical veto runs last and can only remove a finding: it keeps doctors' names,
disease names, doses and the ASHA role.

**`lab/report_9812345670.json`**

| Found | As | Detector | Relies on |
|---|---|---|---|
| Sunita Kerketta | PATIENT_NAME | field | known words (header or key) |
| KMC/26/004411 | MRN | field | known words (header or key) |
| 13/04/2026 | DATE | date | shape of the value |

**`notes/nursing_004411.txt`**

| Found | As | Detector | Relies on |
|---|---|---|---|
| 12/04/2026 | DATE | date | shape of the value |
| Sunita | PATIENT_NAME | honorific | known words (ji, sahab…) |
| Birsa Kerketta | RELATIVE_NAME | relative_label | known words (label) |
| 98123 45670 | PHONE | pattern | shape of the value |
| सुनीता केरकेट्टा | PATIENT_NAME | postposition | known words (ko, ka…) |

Protected by the medical veto: `Birsa`, `Birsa Oraon`

**`registration/patients.csv`**

| Found | As | Detector | Relies on |
|---|---|---|---|
| KMC/26/004411 | MRN | field | known words (header or key) |
| Sunita Kerketta | PATIENT_NAME | field | known words (header or key) |
| 47 | AGE | field | known words (header or key) |
| 14/03/1978 | DOB | field | known words (header or key) |
| Birsa Kerketta | RELATIVE_NAME | field | known words (header or key) |
| 9812345670 | PHONE | field | known words (header or key) |
| 4821 7365 9012 | AADHAAR | field | known words (header or key) |
| Devipur | VILLAGE | field | known words (header or key) |
| 261001 | PIN | field | known words (header or key) |
| KMC/26/004412 | MRN | field | known words (header or key) |
| Tashi Wangdi | PATIENT_NAME | field | known words (header or key) |
| 62 | AGE | field | known words (header or key) |
| 02/01/1963 | DOB | field | known words (header or key) |
| Pema Wangdi | RELATIVE_NAME | field | known words (header or key) |
| 9876501234 | PHONE | field | known words (header or key) |
| 7391 4628 5510 | AADHAAR | field | known words (header or key) |
| Rampurwa | VILLAGE | field | known words (header or key) |
| 261203 | PIN | field | known words (header or key) |

## 4. Learn and sweep

Once every file has been read, the identifiers, ID shapes and villages found anywhere are
searched for everywhere: in other files, in JSON keys and in file names. This is why a
phone number inside a file name is caught even though nothing labels it.

Identifiers learned:

| Value | Type |
|---|---|
| KMC26004411 | MRN |
| KMC26004412 | MRN |
| 482173659012 | AADHAAR |
| 739146285510 | AADHAAR |
| 9812345670 | PHONE |
| 9876501234 | PHONE |

ID shapes learned: `KMC[ /\-_.]?\d{2}[ /\-_.]?\d{6}`

Villages learned: `Devipur`, `Rampurwa`

Found again only because another file revealed them:

| File | Found | As | By |
|---|---|---|---|
| `notes/nursing_004411.txt` | KMC/26/004411 | MRN | known |

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

| Record | Names in it | Strong identifiers | Sex | Pseudonym |
|---|---|---|---|---|
| `lab/report_9812345670.json` doc | Sunita Kerketta | hospital or insurance no. | - | PSN-1T2TTG |
| `notes/nursing_004411.txt` doc | Birsa Kerketta, Sunita, सुनीता केरकेट्टा | hospital or insurance no., phone | - | PSN-1T2TTG |
| `registration/patients.csv` row:0 | Birsa Kerketta, Sunita Kerketta | Aadhaar, hospital or insurance no., phone | F | PSN-1T2TTG |
| `registration/patients.csv` row:1 | Pema Wangdi, Tashi Wangdi | Aadhaar, hospital or insurance no., phone | M | PSN-ZF0ZN9 |

Linking decisions: linked 4, merged_strong 2, patients 2, records 4

## 6. Privacy rules

Every date in a patient's records moves by the same keyed number of days, so intervals
(length of stay, days between tests) stay exact while the real dates are hidden. Birth
dates are removed, ages become bands, villages become their district, PIN codes keep
three digits, and a diagnosis held by only one patient is replaced in every patient record.

| Patient | Date shift |
|---|---|
| PSN-1T2TTG | +167 days |
| PSN-ZF0ZN9 | +71 days |

Age band width chosen for this release: 15 years. Diagnoses held by one patient and suppressed: 0. Patients alone in their (age band, sex, district) group: 2 of 2.

## 7–10. Redact, audit, verify, publish

Replacements are written in place, keeping each file's format: typed tokens such as
`[PHONE]` and `[ID]`, pseudonyms for patients, shifted dates in their original format.
File names holding personal data get a keyed name:

| Input file name | Released as |
|---|---|
| `lab/report_9812345670.json` | `lab/file_c77fe352.json` |
| `notes/nursing_004411.txt` | `notes/file_d4b5ed24.txt` |

The finished text is then searched once
more for every identifier, full name and address phrase detected anywhere in the input,
through disguises and encodings; a hit is repaired, and a file that still fails is
quarantined. Each file is written to a staging folder, read back and checked against the
original's structure before anything is published, and `report.json` is renamed into place
last, so an interrupted run never looks complete.

The released files:

**`sanitized/lab/file_c77fe352.json`**

```json
{
  "patient": {
    "name": "PSN-1T2TTG",
    "uhid": "[ID]"
  },
  "collected": "27/09/2026",
  "results": [
    {
      "test": "Haemoglobin",
      "value": 10.2,
      "unit": "g/dL"
    },
    {
      "test": "Platelet count",
      "value": 150000,
      "unit": "/uL"
    }
  ],
  "referred_by": "Dr. Birsa Oraon"
}
```

**`sanitized/notes/file_d4b5ed24.txt`**

```text
Nursing note - [ID]
26/09/2026 08:00 - PSN-1T2TTG ji ko raat se bukhar hai, temp 101 F. C/O fever x 2 days.
Attendant: [RELATIVE] (husband), mob [PHONE].
Dr. Birsa Oraon reviewed. Adv: Tab Dolo 650 1-0-1 x 3 days.
PSN-1T2TTG को भर्ती किया गया।
```

**`sanitized/registration/patients.csv`**

```csv
UHID,Patient Name,Sex,Age,DOB,Husband/Father Name,Mobile,Aadhaar,Village,District,PIN
[ID],PSN-1T2TTG,F,45-59,[DOB],[RELATIVE],[PHONE],[AADHAAR],Sitapur,Sitapur,261***
[ID],PSN-ZF0ZN9,M,60-74,[DOB],[RELATIVE],[PHONE],[AADHAAR],Sitapur,Sitapur,261***
```

Which record belongs to which patient (`records.jsonl`):

```json
{"file": "sanitized/lab/file_c77fe352.json", "loc": "doc", "patient": "PSN-1T2TTG", "status": "sanitized"}
{"file": "sanitized/notes/file_d4b5ed24.txt", "loc": "doc", "patient": "PSN-1T2TTG", "status": "sanitized"}
{"file": "sanitized/registration/patients.csv", "loc": "row:0", "patient": "PSN-1T2TTG", "status": "generalized"}
{"file": "sanitized/registration/patients.csv", "loc": "row:1", "patient": "PSN-ZF0ZN9", "status": "generalized"}
```

The report (file list omitted):

```json
{
  "status": "complete",
  "counts": {
    "files_total": 4,
    "files_sanitized": 3,
    "files_quarantined": 1,
    "records_processed": 4,
    "identifiers_detected": 23,
    "records_linked": 4,
    "records_generalized": 2,
    "records_suppressed": 0,
    "records_quarantined": 0
  },
  "entities": {
    "AADHAAR": 2,
    "AGE": 2,
    "DATE": 2,
    "DOB": 2,
    "MRN": 4,
    "PATIENT_NAME": 5,
    "PHONE": 3,
    "PIN": 2,
    "RELATIVE_NAME": 3,
    "VILLAGE": 2
  }
}
```

The run log holds only counts, values from fixed lists and digests, and every line is
chained with an HMAC so that an edited log is detected (`tools/verify_log.py`). Selected
lines, without timings, the chain fields and (except for the self-scan) zero counts:

```json
{"event": "run_started", "files": 4, "policy": "43252f89c45a24d5"}
{"event": "linking", "linked": 4, "merged_strong": 2, "patients": 2, "records": 4}
{"age_band_years": 15, "event": "privacy", "patients": 2, "unique_band_sex_district": 2}
{"event": "self_scan", "files_quarantined": 0, "files_repaired": 0, "records_withheld": 0, "repairs": 0}
{"event": "run_finished", "files_quarantined": 1, "files_sanitized": 3, "files_total": 4, "records_linked": 4, "records_processed": 4, "status": "complete"}
```

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

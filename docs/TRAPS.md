# Trap catalogue

Every case where personal data and medical content look alike, written as a **matched
pair**: text the firewall must remove, and similar text it must keep. A detector learns, or
is checked on, the difference only if it sees both sides. The test-data generator (Phase 13)
produces every family below with random wording, in every script it applies to, and the
answer key marks both sides.

**Scorer area**: D = detection (leaks and contextual precision), L = linking, P = privacy
attacks, U = clinical utility, I = integrity (format, file names, side channels).

**Today** says what we know now: *tested* (a unit test covers the pair), *gap* (known to
fail or not handled), *untested* (no test either way). It does not mean the case works
in unseen wording; the generator's held-out ingredients will measure that.

All examples are invented.

## People and names

| ID | Family | Remove | Keep | Area | Today |
|---|---|---|---|---|---|
| N-01 | Doctor and relative share a name | `Attendant: Birsa Kerketta` | `Dr. Birsa Oraon reviewed` | D | tested |
| N-02 | Name that is an ordinary word | `Asha Oraon ko bukhar hai` (patient) | `ASHA worker visited`, `asha hai jaldi theek hogi` | D | tested (role); untested (Hindi word "asha") |
| N-03 | Name that is a medical word | `Patient Name: Parkinson Verma` | `Parkinson's disease`, `known case of Parkinson disease` | D, U | tested |
| N-04 | Eponym with a common surname | a patient surnamed `Addison` | `Addison's disease`, `Down syndrome`, `Bell's palsy` | D, U | tested (eponyms) |
| N-05 | Name with no cue at all, appears once | `Tsering came back for review` | `Tablet came back from pharmacy` | D | **gap**: the canaries that leak in our stress test |
| N-06 | Lower-case name in prose | `ramesh ko kal se khansi` | `ram ram`, `subah shaam` | D | **gap** (T-38) |
| N-07 | Surname first, with comma | `Name: Kerketta, Sunita` | `Diagnosis: Anaemia, mild` | D, L | tested (comma form) |
| N-08 | Initials and abbreviations | `R. K. Oraon`, `Md. Imran`, `Mohd Imran` | `B.P.`, `R.B.S.`, `Md. (Medicine)` | D, L | tested (Md.); untested (initials) |
| N-09 | Forms of address | `Sunita ji`, `Tashi dadaji`, `Pema akka`, `Rani didi` | `ji haan`, `didi ne bola` (nurse) | D | tested (ji, sahab, akka); other forms untested |
| N-10 | Relation words introduce a relative | `S/o Birsa`, `W/o`, `spouse`, `पुत्र`, `ward of` | `ward 4`, `son was born healthy` (no name) | D | tested |
| N-11 | Kinship reference without a name | (nothing to remove) | `uski maa saath hai`, `husband present` | D | untested |
| N-12 | Chat sender | `[12/04/26, 8:13 pm] Pema Mama: …` | `[12/04/26, 8:13 pm] Lab Desk: …` | D | tested |
| N-13 | Name inside a hospital or place name | (a patient's surname alone) | `Kerketta Memorial Hospital`, `Gandhi Chowk` | D, U | tested (facilities) |
| N-14 | Staff names | patient's name in `Received by: patient's son` | `Collected by: Lab tech Meena` (staff) | D | untested: staff names count as non-patient but are they personal? Decide |
| N-15 | Same name, different people (homonyms) | link each to its own patient | never merge them | L | tested |
| N-16 | One person, many spellings and scripts | `Sunita Kerketta`, `SUNITA KERKETTA`, `सुनीता केरकेट्टा`, `Sunitha` | — | L | tested |

## Numbers

| ID | Family | Remove | Keep | Area | Today |
|---|---|---|---|---|---|
| X-01 | Phone in many formats | `98123 45670`, `+91-9812345670`, `098123-45670`, `९८१२३४५६७०` | `Dolo 650`, `BP 120/80` | D | tested |
| X-02 | Aadhaar-like vs lab values | `4821 7365 9012`, `XXXX XXXX 9012` | `Platelets 150000-410000 /uL`, `LOINC 718-7` | D, U | tested |
| X-03 | Dosing patterns | — | `1-0-1`, `0-0-1 x 5 days`, `½-0-½` | D, U | tested |
| X-04 | Hospital numbers with unseen prefixes | `QXR/26/004411`, `IPD-2026-00461`, digits only `00461` with a label | `Bill No BL/26/000235` (not personal), `Batch 22A114` | D, L | tested (shapes); untested (digits-only under an unknown label) |
| X-05 | Insurance and PAN | `PMJAY-UP-1234500000`, `ABCPE1234F` | `Package code SG050A` | D | tested |
| X-06 | PIN code vs other six digits | `261001` near an address | `Platelets 261000`, `Amount 261001` | D, U | tested (in tables); untested (free text) |
| X-07 | Numbers in JSON | `"aadhaar": 482173659012` | `"value": 150000` | D, I | tested |
| X-08 | IDs split or disguised | `9812 34 5670`, `98123-45670`, `9812345670` with zero-width characters | — | D | tested |

## Places

| ID | Family | Remove | Keep | Area | Today |
|---|---|---|---|---|---|
| P-01 | Village vs person vs district | village `Rampur` | person `Ram`, district `Sitapur` | D, U | tested |
| P-02 | Village written in two words | `Sohan Kalan`, `सोहन खुर्द` | `Kalan` alone | D | tested |
| P-03 | Landmarks | `near Hanuman Mandir`, `शिव मंदिर के पास` | `admitted to Ward 4 near nursing station` | D | tested |
| P-04 | Ward of a town vs hospital ward | `C/O Birsa, Ward 8, Mohalla …` | `Bed 29, Ward 4` | D, U | tested |
| P-05 | House numbers | `H.No. 414`, `मकान नं. 133` | `Room 414` | D | tested (Latin and Devanagari) |

## Dates

| ID | Family | Remove or shift | Keep | Area | Today |
|---|---|---|---|---|---|
| T-01 | Birth date, labelled | `DOB 14/03/1978`, `D.O.B.`, `जन्म तिथि` | — | D, P | tested |
| T-02 | Birth date, unlabelled | `14/03/1978` alone in a demographics line | visit date `12/04/2026` | D, P | **gap** (T-39) |
| T-03 | Day-first and month-first | `03/04/2026` in a US-style export | — | U | tested |
| T-04 | Durations are not dates or ages | — | `x 8 yrs`, `since 3 days`, `2 wks` | D, U | tested |
| T-05 | Impossible values | `-2 yrs` | — | P | tested |

## Where personal data hides

| ID | Family | Remove | Keep | Area | Today |
|---|---|---|---|---|---|
| H-01 | File and folder names | `report_9812345670.json`, `Discharge_Sunita_Kerketta.txt` | `lab_reference_ranges.csv` | I | tested |
| H-02 | JSON keys | `{"Sunita Kerketta": {...}}` | `{"Haemoglobin": 10.2}` | D, I | tested |
| H-03 | CSV headers | a header that is a name | `Patient Name` | D, I | untested |
| H-04 | Encoded payloads | base64 or hex of a note with a name | base64 of an image (quarantined, not copied) | D, I | tested |
| H-05 | Escaped text | `Sunita`, `&#83;unita`, `Sunita%20Kerketta` | — | D | tested |
| H-06 | Look-alike letters | `Sunіta` (Cyrillic і), accents `Sunítà` | — | D | tested |
| H-07 | Metadata | `"exported_by": "Sunita Kerketta"`, `source_path` with a name | `"system": "eHospital"` | D, I | tested |
| H-08 | Under an unfamiliar key or header | any of the above under `client`, `label`, `Rogi` | `client: Lab Desk` | D | tested by the scrambled test, not by unit cases |

## Format and structure

| ID | Family | Must hold | Area | Today |
|---|---|---|---|---|
| F-01 | Shifted columns | values one column off their header are typed by their values | D, I | tested |
| F-02 | Split name columns | `First Name` + `Second Name` are one person | L | tested |
| F-03 | Same name written twice in two scripts | `Name` + `नाम` are one person, not first and last | L | tested |
| F-04 | Encodings | UTF-16, UTF-8 with BOM, cp1252 read correctly; legacy Hindi fonts quarantined | I | tested |
| F-05 | Broken or unsupported files | quarantined, never copied | I | tested |
| F-06 | Form layout in text | `Rogi :   Sunita Kerketta    Umar : 47` | D | **gap** for unknown labels (Phase 14) |

## Privacy

| ID | Family | Must hold | Area | Today |
|---|---|---|---|---|
| R-01 | Rare diagnosis with a code | held by one patient → `R69` everywhere in patient records | P, U | tested |
| R-02 | Rare diagnosis without a code | written only in free text | P | **gap** |
| R-03 | Rare diagnosis under an unfamiliar key | `{"dx": {"c": "B55.0"}}` | P | **gap** (T-3E) |
| R-04 | One date shift per patient | every date of a patient moves together, across files | P, U | tested |
| R-05 | Ages near the top | `92` → `90+` | P | tested |

## Questions for review

1. **N-14:** should staff names (lab technicians, nurses) be removed? The scorer treats
   doctors as must-keep; staff are not covered by the published rules.
2. Which regional forms of address, relation words and name patterns are missing? The
   generator will use what is listed here.
3. Are there layouts you have seen in real Indian records (registers, slips, OPD cards)
   that none of these families cover?

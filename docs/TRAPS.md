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
| N-14 | Staff names (decided: remove as `[STAFF]`, never link) | `Staff Nurse Rekha Toppo`, `Sample collected by Meena Kujur`, `Anil Minz, Pharmacist`, `ASHA worker Sunita Lakra`, `"lab_technician": "…"` | `Dr. Anil Minz reviewed`, `Referred by ASHA worker`, `Staff nurse informed the doctor` | D, L | tested |
| N-15 | Same name, different people (homonyms) | link each to its own patient | never merge them | L | tested |
| N-16 | One person, many spellings and scripts | `Sunita Kerketta`, `SUNITA KERKETTA`, `सुनीता केरकेट्टा`, `Sunitha` | — | L | tested |
| N-17 | Newborns named by the mother | `B/o Sunita Kerketta`, `Baby of Sunita`, `Baby Sunita` (the mother's name identifies the child) | `baby weight 2.8 kg` | D, L | untested |
| N-18 | Two patients in one record | mother and child on an MCH card; a ward handover listing several beds | — | D, L | untested: each person must keep their own role and pseudonym |

## Regional forms of address, relation words and name patterns

These extend N-08 to N-10 across India. They come from general knowledge, not from records
we have seen, and the generator will use them on both sides of each pair (a form of address
with a name to remove, and the same word used on its own, to keep).

**Forms of address**

| Region or community | Before or after a name | Used on its own (keep) |
|---|---|---|
| Hindi belt | ji, sahab / saab, babu, babuji, bauji, bhaiya, didi, behenji, bhabhi, mataji, pitaji, amma ji, chacha, mama, mausi, bua, dadi, nani | `ji haan`, `bhaiya bole` |
| Punjabi | ji, paaji, bhaji, beji, bibi, bhenji, sardar, sardarni | `bibi ne bataya` |
| Bengali | da, dada, di, didi, babu (`Haren babu`), mashi, kaku, jethu, boudi | `dada aaye` |
| Gujarati | bhai and ben joined to the name (`Ramesh-bhai`, `Sunita-ben`, `Rameshbhai`), kaka, masi | `ben ne kaha` |
| Marathi | rao, saheb, tai, bai (`Sunita-bai`), dada, kaka, aai, baba | `tai aali` |
| Tamil | anna, akka, thambi, amma, appa, paati, thatha, avargal; Thiru, Thirumathi, Selvi before the name | `amma came` |
| Telugu | garu, anna, akka, amma, nanna, babai | `garu said` |
| Kannada | avaru / avare, anna, akka | — |
| Malayalam | chettan / chetta, chechi, ammachi | — |
| Muslim communities | sahab, janab, begum, bibi, khala, khalu, apa, baji, phuphi | `begum sahiba` (title only) |
| Mizo | Pu (men), Pi (women) before the name | — |
| Khasi | Bah (men), Kong (women) before the name | — |
| Formal titles | Shri, Shrimati, Smt, Kumari, Sushri, Late / Swargiya, Sri, Selvan | — |

**Relation words** (the name after them is a relative; R-rows add the cases not in N-10)

| Form | Meaning | Keep-side clash |
|---|---|---|
| S/o, D/o, W/o, C/O, M/o, F/o, H/o, Wd/o | son, daughter, wife, care of, mother, father, husband, widow of | `H/O fever` (history of), `C/O cough` (complains of) |
| B/o | baby of: the **mother's** name follows | — |
| पुत्र, पुत्री, पत्नी, पति, सुपुत्र, सुपुत्री, आत्मज, आत्मजा, धर्मपत्नी, बेवा | Hindi relations | `पति साथ आए` (no name) |
| walad / वल्द, bint, zauja / ज़ौजा | Urdu legal forms: son of, daughter of, wife of | — |
| `X ki wife`, `X ka beta`, `X ke pati`, `X ki beti`, `beta of X` | Hinglish relations, name before or after | `beta theek hai` |

**Name patterns**

| Pattern | Example (invented) | Why it is hard |
|---|---|---|
| Initials first (Tamil Nadu, Andhra) | `R. Karuppan`, `K.S. Esakki` | the initial is the father's or village name; a single word follows |
| Father's name in the middle (Maharashtra, Gujarat) | `Sunita Ramesh Patil`, `Rameshbhai Kantilal Patel` | three words; the middle one is another person |
| Middle names Chandra, Kumar, Nath, Lal, Prasad | `Haren Chandra Das` | generic words that are not identifying alone |
| Generic surnames | `Harpreet Kaur`, `Gurmeet Singh`, Meitei women `Thoibi Devi`, `Ibemhal Chanu` | the surname identifies nobody; the given name must be kept for linking |
| Muslim name parts | `Md.`, `Mohd`, `Sk`, `Shaikh`, `-uddin`, `-ullah`, `bin` | abbreviations and joined forms |
| North-east and Adivasi names | `Lalremruata`, `Vanlalhriata`, `Birsa Munda`, `Thoibisana` | rarely in Hindi-belt name lists |
| One-word names | `Ramu`, `Sukhia` | no surname, often lower case in notes |
| Nicknames | `Pappu`, `Guddu`, `Bablu`, `Munna`, `Chhotu` | look like ordinary words |
| Names that are words | `Asha`, `Kiran`, `Suraj`, `Lata`, `Ganga`, `Lucky`, `Happy`, `Rose` | the same word in the keep side |
| Caste or title words | `Pandit`, `Chaudhary`, `Thakur` used as a name or as an address | — |

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
| X-09 | Other government and programme IDs | voter ID (EPIC), ration card, driving licence, passport, PM-JAY family ID, RCH / MCTS ID (looks like an Aadhaar), Nikshay TB ID, ESIC number, UDID, CoWIN beneficiary reference, medico-legal case (MLC) number, police FIR number, vehicle number in ambulance or MLC records, bank account and IFSC | lab accession numbers, batch numbers, bed numbers | D | untested: exact formats to be confirmed from public specifications before the generator uses them |

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

## Record layouts

Layouts we have not seen in the practice data, from Indian public-health and hospital practice.
The generator builds each as a layout ingredient; several are held out for testing.

| ID | Layout | What makes it hard |
|---|---|---|
| L-01 | OPD card or slip | printed header, short `Label : value` fields on one line, token number |
| L-02 | Admission or OPD register (one row per patient, HMIS-style columns: S.No, Date, Reg No, Name, Age/Sex, Address, Diagnosis, Outcome) | many patients in one table, wide rows, abbreviations |
| L-03 | IPD case sheet with daily progress notes | the same patient over many dated entries, staff signatures |
| L-04 | Discharge card (short, handwritten style typed later) | free text with few labels |
| L-05 | Referral slip (PHC or CHC to district hospital; ASHA referral) | two facilities, ASHA and ANM names, village |
| L-06 | Lab requisition and report, or a lab register with many patients | technician names, sample IDs next to patient IDs |
| L-07 | Pharmacy dispensing register or bill | medicine lists beside names and amounts |
| L-08 | Mother and child (MCH) card, RCH register | two patients in one record, pregnancy dates (LMP, EDD), immunisation dates |
| L-09 | TB (Nikshay) register | programme ID, treatment start dates, DOTS provider names |
| L-10 | Medico-legal register | MLC number, police station, brought-by names, injury descriptions |
| L-11 | Ambulance (108) trip sheet | caller's phone, pickup place, times |
| L-12 | Blood bank register | donor and recipient names in one row, blood group |
| L-13 | Death register or cause-of-death certificate | the deceased, informant and cause of death |
| L-14 | Health camp or school screening list | child, parent, school or village, many rows |
| L-15 | Vaccination certificate | beneficiary reference number, dates, vaccinator |
| L-16 | Ward census or nurse handover note | several patients by bed in one paragraph |
| L-17 | Staff duty roster | only staff names (all removed) and dates |
| L-18 | Telemedicine prescription | doctor and patient in different places, consultation ID |
| L-19 | Insurance pre-authorisation form | family ID, card number, package codes |
| L-20 | Spreadsheet exports with two header rows, merged headers or totals rows | the header is not the first row |
| L-21 | Text tables laid out with spaces or pipes | columns without a delimiter |
| L-22 | Text copied from a scan (OCR) | broken words, `0`/`O` and `1`/`l` confusions, lost line breaks |

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

## What the practice data contains (read 26 Nov 2026)

About 30 document templates from four private hospitals and one district hospital, all in
Uttar Pradesh: discharge summaries (English and Devanagari), short discharge notes, nursing
notes, a visitor log, OPD prescriptions (English and Devanagari), WhatsApp lab forwards, lab
reports (JSON) and lab result tables, OPD exports (JSON), registration tables (CSV, cp1252
CSV, UTF-16 TSV, JSON), claims tables, FHIR bundles, and a set of broken or unsupported files.
Names are mostly from the Hindi belt, with a few Punjabi, Muslim, South Indian and North-east
names, mainly among canaries.

Patterns seen there that the families above did not yet name:

| ID | Pattern | Example shape (invented) | Family |
|---|---|---|---|
| S-01 | Hinglish possessive after a name | `Ramu bhai ki condition stable hai`, `Leela ji ka BP 130/80`, `Leela amma ki` | N-09 |
| S-02 | Chat sender named by kinship | `[02/03/26, 9:10 pm] Kamal Chachu:` | N-12 |
| S-03 | Relation that points the other way | `Attendant (S/o Ramu)`: the attendant is Ramu's son | N-10 |
| S-04 | Two spellings of one name in one note | `Brijmohan ji` … `Brajmohan Yadav` | N-16 |
| S-05 | One patient at two hospitals with two hospital numbers | `JKH-0012345` and `SMH/26/004411` | L, X-04 |
| S-06 | Hospital number plus name as a column header | `time,KMC-0012 Leela,KMC-0013 Ramu` | H-03 |
| S-07 | Index keyed by patient name | `{"by_name": {"Leela Oraon": "KMC-0012"}}` | H-02 |
| S-08 | Name inside an export path in metadata | `"source_path": "D:\\exports\\Leela_Oraon_0012\\visits.json"` | H-07 |
| S-09 | Witness name and phone inside a base64 attachment | consent form text encoded in base64 | H-04 |
| S-10 | Caller in a free-text remark | `called by Leela Oraon 98765-01234` | N-05 |
| S-11 | Date without a year | `Visit: 5 September` | T-03 |
| S-12 | US-format dates in one hospital's files only | `03/23/2026` | T-03 |
| S-13 | ICD code after K/C/O | `K/C/O: I10` | R-01 |
| S-14 | Doctors shared across hospitals, sharing given names with relatives | `Dr. Ramu Saxena` and relative `Ramu Oraon` | N-01 |
| S-15 | Upper-case names in lab tables | `LEELA ORAON` | N-16 |

The generator reuses these on its training side; its test side adds the families and layouts
above that the practice data does not contain (staff, regional forms, other ID types, the
L-layouts, multi-patient records).

## Decisions and sources

- **Staff (N-14):** removed as `[STAFF]` and never used for linking (decided 26 Nov 2026).
  Doctors stay, as the scorer requires; role words such as "ASHA worker" stay.
- The regional forms, name patterns, ID types and layouts above come from general
  knowledge of Indian records, not from data we have seen. Formats of the ID types in X-09
  must be checked against public specifications before the generator produces them.

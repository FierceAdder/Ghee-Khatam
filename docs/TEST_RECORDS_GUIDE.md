# Writing test records for the firewall

Thank you for helping. We need hospital records written by someone who has **not** seen our
code or the practice data, so we can find out how the firewall does on writing it has never
met. Your records are run once, before we change anything, and the result is reported as it is.

## The one rule

**Everything must be invented.** Make up every name, phone number, ID, address and date. Do
not copy a real person, a real record or a real document, even with changes.

## What to write

20 to 50 records, in any mix of the kinds below. Write them the way you think a clerk,
nurse, doctor or lab in a small Indian hospital would, in your own style. Variety matters
more than polish.

| Kind | Format | Ideas |
|---|---|---|
| Registration list | CSV or a spreadsheet saved as CSV | your own column names, in English, Hindi or Hinglish |
| Discharge summary or OPD note | plain text | headings, free sentences, abbreviations |
| Prescription | plain text | medicines, doses, advice, the doctor's name |
| Nursing or chat note | plain text | Hinglish, WhatsApp-style messages, nicknames |
| Lab report | JSON or text | test names, values, units, who it is for |

Please include some of these, because they are where software goes wrong:

- people who appear in **several** records, written differently each time (spelling, script,
  initials, surname first, with and without "ji", "Md.", "Smt.");
- a person mentioned **only once**, in a sentence, with no "Name:" label;
- a doctor and a relative with the **same** name;
- words that look like names but are not: medicines, diseases named after people
  ("Parkinson's disease"), "ASHA worker", places;
- numbers that look like phone or ID numbers but are not: doses ("Dolo 650"), lab values,
  "1-0-1";
- Hindi in Devanagari, and Hindi written in English letters.

## The answer sheet

For each record, list every piece of personal data in `answers.csv`:

| Column | What to write | Example |
|---|---|---|
| `file` | the file name | `opd_note_3.txt` |
| `record` | `row:N` for a CSV row (first data row is `row:0`), else `doc` | `row:2` |
| `text` | the exact text as it appears in the file | `Meren Longkumer` |
| `type` | one of: PATIENT_NAME, RELATIVE_NAME, PHONE, AADHAAR, ABHA_NUMBER, MRN (hospital number), INSURANCE_ID, PAN, EMAIL, DOB, HOUSE, LOCALITY, LANDMARK, VILLAGE, PIN | `PATIENT_NAME` |
| `person` | a number you choose for each invented person, the same in every record | `7` |
| `role` | `patient` or `relative` | `patient` |

Also list text that must **stay**, so we can see whether it is wrongly removed, in
`keep.csv` with columns `file`, `record`, `text` (for example `Dr. Meren Ao`, `Dolo 650 BD x 5 days`).

## How to send it

Put the files and the two sheets in one folder and send it to us directly. It is kept
outside the code repository and is not used to tune anything before its first run.

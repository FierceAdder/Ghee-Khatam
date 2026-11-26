#!/usr/bin/env python3
"""Make an altered copy of the practice data with a matching answer key (development only).

    python3 tools/mutate.py --out /tmp/altered --seed 1 [--toolkit ../Secret-Wards-Toolkit]
    python3 main.py --input /tmp/altered/input --output /tmp/altered/release
    python3 ../Secret-Wards-Toolkit/check_dev.py --output /tmp/altered/release \\
        --truth /tmp/altered/truth/truth.json

Stands in for the "unseen formats" dataset: every patient and relative gets a new name,
labels, column headers and JSON keys are renamed, hospital-number prefixes and date formats
change, some files change encoding and folders are renamed. Every change is a text rewrite
applied identically to the files and to the answer key, so the key stays exact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import shutil
from pathlib import Path

from utility import ROOT, default_truth

from wards.dates import DMY, MDY, find_dates
from wards.lexicon import HONORIFICS_LATIN, Lexicon
from wards.normalize import is_devanagari

# Invented or uncommon names, most of them absent from the gazetteers, as (Latin, Devanagari).
NEW_NAMES = [tuple(pair.split(":")) for pair in """
Lalremruata:लालरेमरुआता Zothanpuii:ज़ोथनपुई Vanlalhriata:वनलालह्रिआता Chingkhei:चिंगखेई
Thoibisana:थोइबीसना Leishangthem:लैशांगथेम Rinzing:रिनज़िंग Pasangkit:पासांगकित
Norbula:नोरबुला Wangchuma:वांगचुमा Imkongla:इमकोंगला Temjenwati:तेमजेनवती
Aosenla:आओसेनला Moatoshi:मोआतोशी Bendangnaro:बेंदांगनारो Sentila:सेंतिला
Kerhileu:केरहिलेउ Vikheho:विखेहो Medongul:मेदोंगुल Zhasivino:झासिविनो
Phyobenthung:फ्योबेनथुंग Yanbemo:यानबेमो Lipokmar:लिपोकमार Nchumbeni:नचुम्बेनी
Daphishisha:दाफिशीशा Banrilin:बानरिलिन Kitboklang:कितबोकलांग Iohborlang:इओबोरलांग
Wanboklin:वानबोकलिन Pynhunlang:पिनहुनलांग Hmingthansanga:ह्मिंगथनसांगा Ramdinliana:रामदिनलिआना
Khumanthem:खुमानथेम Yumnamcha:युमनमचा Oinam:ओइनम Sagolsem:सागोलसेम
Ningthouja:निंगथौजा Heigrujam:हेइग्रुजम Moirangthem:मोइरांगथेम Pukhrambam:पुखरामबम
Kharmawlong:खरमावलोंग Mawphlang:मावफलांग Sohliya:सोहलिया Nongkynrih:नोंगकिनरिह
Jongte:जोंगते Hrahsel:ह्राहसेल Ralte:राल्ते Sailo:साइलो Chhangte:छांगते Pachuau:पचुआउ
Gyaltsen:ग्यालत्सेन Lhamu:ल्हामू Choden:चोदेन Dendup:देंदुप Yougan:योउगन Tobgay:तोबगे
Esakki:एसक्की Karuppan:करुप्पन Veerammal:वीरम्मल Chinnasamy:चिन्नासामी
Ayyavu:अय्यावु Ponnuthai:पोन्नुथाई Madasamy:मदासामी Sudalai:सुदलई
Birsingh:बीरसिंग Sukurmoni:सुकुरमोनी Hembrom:हेम्ब्रोम Tudu:टुडू Kisku:किस्कू
Marandi:मरांडी Soren:सोरेन Baskey:बासके Besra:बेसरा Hansdak:हांसदाक
""".split()]
_KEEP_TOKENS = HONORIFICS_LATIN | {"kumar", "kumari", "devi", "singh", "kaur", "bano", "mohammad",
                                   "md", "mohd", "begum", "khatoon", "khatun", "lal"}

ID_PREFIXES = (("SNJ/26/", "SMH-26-"), ("SNJ-26-", "SMH_26_"), ("JJH-", "KGH/"),
               ("UHID-", "PID/"), ("DHH2026-", "HRD26/"), ("DHH-IPD-", "HRD-INP-"),
               ("IPD-2026-", "INP/26/"), ("ipd-2026-", "inp-26-"), ("IP/26/", "ADMT-26-"),
               ("IP-26-", "ADMT_26_"), ("ip-26-", "admt-26-"), ("ADM", "AN"), ("adm", "an"))

LABELS = (
    ("Patient Name:", "Name of Pt:"), ("Name:", "Pt Name -"), ("IPD No:", "Inpatient No.:"),
    ("UHID:", "MR No:"), ("Reg No:", "Patient ID:"), ("Mobile:", "Contact No.:"),
    ("Mob.", "Ph."), ("Mob:", "Phone:"), ("Address:", "Residential Address:"),
    ("Attendant:", "Informant:"), ("S/o ", "Son of "), ("W/o ", "Wife of "),
    ("D/o ", "Daughter of "), ("Age/Sex:", "Age & Sex:"), ("Vill. ", "Village "),
    ("Distt. ", "District "), ("मरीज़ का नाम:", "रोगी का नाम:"), ("पति का नाम:", "पति:"),
    ("पिता का नाम:", "पिता:"), ("मोबाइल:", "दूरभाष:"), ("पता:", "निवास:"),
    ("ग्राम:", "गांव:"), ("पंजीकरण सं.:", "पंजीकरण क्रमांक:"), ("Report of ", "Reports for "),
)

HEADERS = {
    "Patient Name": "Pt Full Name", "patient_name": "beneficiary", "Mobile": "Contact#",
    "Mobile No": "Cell No", "Phone": "Contact Number", "Aadhaar No": "UIDAI No",
    "Aadhar": "Adhaar Number", "ABHA No": "Health ID", "Village/Mohalla": "Gram/Mohalla",
    "Relative Name": "Guardian's Name", "Guardian": "Next of Kin", "PIN": "Postal Code",
    "Pin Code": "PIN", "Zip": "Pincode", "DOB": "Birth Dt", "Age": "Age (Yrs)",
    "Reg Date": "Enrolled On Date", "Naam": "Mareez ka Naam", "Gaon": "Gram",
    "Jila": "Zila", "uhid": "mr_number", "UHID": "MRN", "PatientID": "MR Number",
    "LastName": "Surname", "FirstName": "Given Name", "City": "Village", "Street": "Addr",
    "payer_pan": "pan_no", "pmjay_id": "ayushman_id", "age_sex": "age_gender",
}
JSON_KEYS = {
    "patient_ref": "mrn", "mobile": "contact_number", "phone": "contact_no",
    "hospital_uhid": "mr_no", "uhid": "hospital_number", "guardian": "next_of_kin",
    "visit_date": "date_of_visit", "village": "gram", "pin": "postal_code",
    "dob": "date_of_birth", "address_note": "residence_note", "patient_name": "beneficiary_name",
    "aadhar_no": "uidai", "referred_by": "referring_doctor", "remarks": "comments",
}
# The hard level: wording chosen to be unfamiliar, including cues removed altogether.
HARD_LABELS = (
    ("Patient Name:", "Beneficiary -"), ("Name:", "Client"), ("IPD No:", "Adm Ref"),
    ("UHID:", "Health Rec"), ("Reg No:", "Slip"), ("Mobile:", "Reach at"),
    ("Mob.", "reach"), ("Mob:", "Reach"), ("Address:", "Stays at"),
    ("Attendant:", "Care giver"), ("S/o ", "ward of "), ("W/o ", "spouse "),
    ("D/o ", "child of "), ("Age/Sex:", "Yrs/Sex"), ("Report of ", "Enclosed for "),
    ("मरीज़ का नाम:", "लाभार्थी"), ("पति का नाम:", "घरवाले"), ("पिता का नाम:", "अभिभावक का विवरण"),
    ("मोबाइल:", "सूचना"), ("पता:", "रहवास"), ("ग्राम:", "बस्ती"),
)
HARD_HEADERS = {
    "Patient Name": "Beneficiary", "patient_name": "client", "Mobile": "Reach", "Mobile No":
    "Reach No", "Phone": "Tel", "Aadhaar No": "Govt ID", "Aadhar": "ID Proof",
    "ABHA No": "Health Stack", "Village/Mohalla": "Habitation", "Relative Name": "Care Giver",
    "Guardian": "Backup Person", "PIN": "Post Index", "DOB": "Born", "Age": "Completed Yrs",
    "Naam": "Labharthi", "Gaon": "Habitation", "uhid": "rec", "UHID": "Rec",
    "PatientID": "Rec", "LastName": "Name2", "FirstName": "Name1", "Street": "Location",
    "City": "Habitation",
}
HARD_JSON_KEYS = {
    "patient_ref": "ref", "mobile": "reach", "phone": "reach", "hospital_uhid": "rec",
    "uhid": "rec", "guardian": "caregiver", "village": "habitation", "address_note": "where",
    "patient_name": "client", "patient": "subject_person", "aadhar_no": "govt_id",
    "name": "label",
}

# The held-out level: written before the detection fixes and never used to find failures,
# so one measurement stays independent of the fixes. Do not tune against it.
HOLDOUT_LABELS = (
    ("Patient Name:", "Name of the sick person:"), ("Name:", "Seen:"),
    ("IPD No:", "Bed Admission #"), ("UHID:", "Card:"), ("Reg No:", "Token No"),
    ("Mobile:", "WhatsApp:"), ("Mob.", "cell"), ("Mob:", "Cell:"), ("Address:", "Lives at:"),
    ("Attendant:", "Accompanied by:"), ("W/o ", "wife of "), ("D/o ", "daughter of "),
    ("Age/Sex:", "A/S:"),
    ("मरीज़ का नाम:", "रुग्ण:"), ("पति का नाम:", "पति श्री"), ("पिता का नाम:", "पिता श्री"),
    ("मोबाइल:", "संपर्क:"), ("पता:", "निवासी:"), ("ग्राम:", "गाँव -"),
)
HOLDOUT_HEADERS = {
    "Patient Name": "Name of Beneficiary", "patient_name": "person", "Mobile": "WhatsApp",
    "Mobile No": "Alt Contact", "Aadhaar No": "UID", "Aadhar": "UID Number",
    "Village/Mohalla": "Native Place", "Relative Name": "Attendant Name", "DOB": "D.O.B.",
    "Age": "Age in Yrs", "PIN": "Area PIN", "Naam": "Rogi", "Gaon": "Mauza", "uhid": "card_no",
    "UHID": "Card No", "PatientID": "Card ID", "LastName": "Second Name",
    "FirstName": "First Name", "City": "Native Place", "Street": "Locality",
}
HOLDOUT_JSON_KEYS = {
    "patient_ref": "card_no", "mobile": "whatsapp", "hospital_uhid": "card_number",
    "uhid": "card", "guardian": "attendant", "village": "native_place",
    "patient_name": "person", "address_note": "lives_at", "aadhar_no": "uid_number",
}
# Forms of address are swapped word for word, in the files and in the answer key.
HARD_ADDRESS_WORDS = {"sahab": "dadaji", "behenji": "akka", "ji": "garu"}
HOLDOUT_ADDRESS_WORDS = {"sahab": "uncle", "ji": "aunty"}
LEVELS = {"easy": (LABELS, HEADERS, JSON_KEYS, {}),
          "hard": (HARD_LABELS, HARD_HEADERS, HARD_JSON_KEYS, HARD_ADDRESS_WORDS),
          "holdout": (HOLDOUT_LABELS, HOLDOUT_HEADERS, HOLDOUT_JSON_KEYS,
                      HOLDOUT_ADDRESS_WORDS)}

FOLDERS = {"registration": "patient_master", "discharge": "ds", "notes": "nursing",
           "prescriptions": "rx", "billing": "claims", "lab": "laboratory", "opd": "outpatient"}


class Mutator:
    def __init__(self, truth: dict, seed: int, level: str = "easy") -> None:
        self.truth = truth
        self.label_map, self.header_map, self.key_map, self.address_words = LEVELS[level]
        self.rng = random.Random(seed)
        self.lexicon = Lexicon.load()
        self.names = self._name_map()

    def _name_map(self) -> dict[str, str]:
        """Old word -> new word; spellings of one name (either script) share a new name."""
        words = set()
        for spec in self.truth["files"].values():
            for unit in spec["units"].values():
                for entity in unit["entities"]:
                    if entity["type"] in ("PATIENT_NAME", "RELATIVE_NAME", "CANARY"):
                        words.update(re.findall(r"[A-Za-z]+|[ऀ-ॣॱ-ॿ]+",
                                                entity["surface"]))
        groups: dict[str, list[str]] = {}
        for word in sorted(words):
            if word.lower() in _KEEP_TOKENS or self.lexicon.is_stopword(word) or len(word) < 3 \
                    or self.lexicon.canonical(word) in _KEEP_TOKENS:
                continue
            groups.setdefault(self.lexicon.name_key(word), []).append(word)
        pool = list(NEW_NAMES)
        self.rng.shuffle(pool)
        mapping = {}
        for number, (_, spellings) in enumerate(sorted(groups.items())):
            latin, deva = pool[number % len(pool)]
            if number >= len(pool):                    # keep new names distinct
                latin, deva = latin + pool[number - len(pool)][0][:3].lower(), \
                    deva + pool[number - len(pool)][1][:2]
            for word in spellings:
                if is_devanagari(word):
                    mapping[word] = deva
                else:
                    mapping[word.lower()] = latin
        return mapping

    # ------------------------------------------------------------ text rewrites

    def rename(self, text: str) -> str:
        def swap(m: re.Match[str]) -> str:
            word = m.group(0)
            new = self.names.get(word if is_devanagari(word) else word.lower())
            if new is None:
                return word
            if word.isupper() and not is_devanagari(word):
                return new.upper()
            return new.lower() if word.islower() else new

        return re.sub(r"(?<![A-Za-z])[A-Za-z]+(?![A-Za-z])|[ऀ-ॣॱ-ॿ]+",
                      swap, text)

    @staticmethod
    def identifiers(text: str) -> str:
        for old, new in ID_PREFIXES:
            if old.isalpha():
                text = re.sub(rf"(?<![A-Za-z]){old}(?=\d{{6}})", new, text)
            else:
                text = text.replace(old, new)
        return text

    def labels(self, text: str) -> str:
        for old, new in self.label_map:
            text = text.replace(old, new)
        for old, new in self.address_words.items():
            text = re.sub(rf"(?<![A-Za-z_]){old}(?![A-Za-z_])", new, text)
        return text

    @staticmethod
    def dates(text: str, order: str) -> str:
        """Rewrite numeric dates in another style, keeping the day they name."""
        out = text
        for match in reversed(find_dates(text, typed=False)):
            value = match.resolve(order, 2026)
            if value is None or match.year is None or match.year[1] - match.year[0] != 4:
                continue
            if "-" in match.text and match.text[:4].isdigit():
                new = value.strftime("%d.%m.%Y")
            else:
                new = value.strftime("%d-%b-%Y")
            out = out[:match.start] + new + out[match.end:]
        return out

    def content(self, text: str, order: str, restyle_dates: bool) -> str:
        text = self.identifiers(self.rename(self.labels(text)))
        return self.dates(text, order) if restyle_dates else text

    # ------------------------------------------------------------------ files

    def headers(self, text: str, delimiter: str) -> str:
        first, newline, rest = text.partition("\n")
        carriage = "\r" if first.endswith("\r") else ""
        row = next(csv.reader([first.rstrip("\r")], delimiter=delimiter), [])
        buffer = io.StringIO()
        csv.writer(buffer, delimiter=delimiter, lineterminator="").writerow(
            [self.header_map.get(cell, cell) for cell in row])
        return buffer.getvalue() + carriage + newline + rest

    def keys(self, text: str) -> str:
        return re.sub(r'"([A-Za-z_]+)"(\s*:)',
                      lambda m: f'"{self.key_map.get(m.group(1), m.group(1))}"{m.group(2)}', text)

    def path(self, rel: str) -> str:
        folder, _, name = rel.partition("/")
        stem, dot, suffix = name.rpartition(".")
        stem = self.identifiers(self.rename(stem)).replace("/", "_")
        return f"{FOLDERS.get(folder, folder)}/{stem}{dot}{suffix}"

    @staticmethod
    def unmatched(destination: Path) -> tuple[int, int]:
        """Answer-key surfaces that do not occur in the altered input (should be none).

        A leak check looks for these exact strings, so a surface that drifted from its file
        would make the firewall look clean when it is not.
        """
        truth = json.loads((destination / "truth" / "truth.json").read_text(encoding="utf-8"))
        missing = total = 0
        for rel, spec in truth["files"].items():
            path = destination / "input" / rel
            if spec.get("expect") != "sanitize" or spec["format"] not in ("csv", "tsv", "text",
                                                                          "json", "fhir"):
                continue
            text = path.read_bytes().decode(spec.get("encoding") or "utf-8")
            if spec["format"] in ("json", "fhir"):
                text += "\n" + json.dumps(json.loads(text), ensure_ascii=False)
            for unit in spec["units"].values():
                for entity in unit["entities"]:
                    if entity["channel"] != "content":
                        continue
                    total += 1
                    missing += entity["surface"] not in text
        return missing, total

    def run(self, source: Path, destination: Path) -> None:
        shutil.rmtree(destination, ignore_errors=True)
        files = {}
        for number, (rel, spec) in enumerate(sorted(self.truth["files"].items())):
            raw = (source / rel).read_bytes()
            order = MDY if spec.get("date_hint") == "mdy" else DMY
            new_rel, restyle = rel, False
            encoding = spec.get("encoding") or "utf-8"
            if spec.get("expect") == "sanitize" and not spec.get("clean") \
                    and spec["format"] in ("csv", "tsv", "json", "text"):
                text = raw.decode(encoding)
                # JSON dates stay ISO-like in half of the files; text and tables restyle
                restyle = number % 2 == 0
                new = self.content(text, order, restyle)
                if spec["format"] in ("csv", "tsv"):
                    new = self.headers(new, spec["delimiter"])
                    new = new.replace("(MM/DD/YYYY)", "") if restyle else new
                elif spec["format"] == "json":
                    new = self.keys(new)
                if encoding == "utf-8" and number % 3 == 0:
                    encoding = "utf-16" if number % 6 == 0 else "utf-8-sig"
                raw = new.encode(encoding)
                new_rel = self.path(rel)
            elif spec["format"] == "fhir":
                restyle = False
                raw = self.identifiers(self.rename(raw.decode("utf-8"))).encode("utf-8")
                new_rel = self.path(rel)
            target = destination / "input" / new_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            files[new_rel] = self._truth_entry(spec, new_rel, raw, encoding, order, restyle)
        altered = dict(self.truth, files=files)
        (destination / "truth").mkdir(parents=True, exist_ok=True)
        (destination / "truth" / "truth.json").write_text(
            json.dumps(altered, ensure_ascii=False, indent=1), encoding="utf-8")

    def _truth_entry(self, spec: dict, rel: str, raw: bytes, encoding: str, order: str,
                     restyle: bool) -> dict:
        entry = json.loads(json.dumps(spec))
        entry.update(path=rel, sha256=hashlib.sha256(raw).hexdigest(), encoding=encoding)
        if restyle:
            entry["date_hint"] = DMY
        untouched = spec.get("clean") or spec.get("expect") != "sanitize" \
            and spec["format"] != "fhir"
        if untouched:
            return entry

        def rewrite(value: str) -> str:
            value = self.identifiers(self.rename(self.labels(value)))
            return self.dates(value, order) if restyle else value

        for unit in entry["units"].values():
            for entity in unit["entities"]:
                entity["surface"] = rewrite(entity["surface"])
                if entity.get("tokens"):
                    entity["tokens"] = [self.rename(t.capitalize()).casefold()
                                        for t in entity["tokens"]]
                if entity.get("match") == "text":
                    entity["key"] = entity["surface"].casefold()
            for probe in unit.get("preserve") or []:
                probe["text"] = rewrite(probe["text"])
        return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--toolkit")
    ap.add_argument("--level", choices=sorted(LEVELS), default="easy",
                    help="hard: unfamiliar wording, fewer cues; holdout: do not tune on it")
    args = ap.parse_args()
    truth_path = default_truth(args.toolkit)
    destination = Path(args.out).resolve()
    if ROOT == destination or destination in ROOT.parents \
            or destination == truth_path.parents[2].resolve():
        raise SystemExit("refusing to write over the repository or the toolkit")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    Mutator(truth, args.seed, args.level).run(truth_path.parent.parent / "input", destination)
    missing, total = Mutator.unmatched(destination)
    print(f"altered copy written; answer-key surfaces not found in their file: "
          f"{missing} of {total}")
    return 1 if missing > total * 0.005 else 0


if __name__ == "__main__":
    raise SystemExit(main())

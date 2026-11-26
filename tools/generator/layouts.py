"""Document layouts. Each builds the text of one file and its answer key together.

Training layouts resemble the practice data; test layouts are kinds of record the practice
data does not contain (docs/TRAPS.md, L-01 to L-22). Every value that is personal is passed
through ``doc.ent`` as it is written, so the answer key cannot drift from the file.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import random

from generator import vocab
from generator.annotate import Doc
from generator.world import Hospital, Person, World

DEVA_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


class Ctx:
    """Choices that depend on the split: labels, separators, date and number formats."""

    def __init__(self, world: World, rng: random.Random) -> None:
        self.world, self.rng, self.split = world, rng, world.split
        self.side = 0 if world.split == "train" else 1

    def label(self, field: str, deva: bool = False) -> str:
        options = vocab.LABELS[field][self.side]
        pool = [o for o in options if any("\u0900" <= c <= "\u097f" for c in o) == deva]
        return self.rng.choice(pool or options)

    def key(self, field: str) -> str:
        return self.rng.choice(vocab.JSON_KEYS[field][self.side])

    def sep(self) -> str:
        return ": " if self.side == 0 else self.rng.choice([" - ", " :- ", " : ", "    "])

    def date(self, day: dt.date, hospital: Hospital | None = None) -> str:
        if hospital is not None and hospital.us_dates:
            return day.strftime("%m/%d/%Y")
        formats = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"] if self.side == 0 else \
            ["%d %b %Y", "%d-%b-%y", "%d/%m/%y"]
        return day.strftime(self.rng.choice(formats))

    def phone(self, number: str) -> str:
        styles = [f"+91 {number[:5]} {number[5:]}", f"{number[:5]}-{number[5:]}", number] \
            if self.side == 0 else \
            [f"+91-{number[:3]}-{number[3:6]}-{number[6:]}", f"0{number}",
             f"{number[:4]} {number[4:7]} {number[7:]}"]
        return self.rng.choice(styles)

    def aadhaar(self, number: str) -> str:
        return self.rng.choice([f"{number[:4]} {number[4:8]} {number[8:]}", number,
                                f"{number[:4]}-{number[4:8]}-{number[8:]}"])

    def visit(self) -> dt.date:
        return dt.date(2026, 1, 5) + dt.timedelta(days=self.rng.randint(0, 300))

    def doctor(self) -> Person:
        return self.rng.choice(self.world.doctors)

    def hospital_of(self, person: Person) -> tuple[Hospital, str]:
        return self.rng.choice(self.world.ids_of(person))


def _age_sex(p: Person, deva: bool = False) -> str:
    if deva:
        return f"{p.age} वर्ष / {'महिला' if p.gender == 'F' else 'पुरुष'}"
    return f"{p.age} Yrs/{p.gender}"


def _variant(word: str) -> str:
    """Another common spelling of the same name (trap S-04)."""
    for a, b in (("ee", "i"), ("oo", "u"), ("i", "ee"), ("u", "oo")):
        if a in word[1:]:
            return word[0] + word[1:].replace(a, b, 1)
    return word + "a"


def _dx(doc: Doc, loc: str, p: Person) -> str:
    parts = []
    for name, code in p.diagnoses:
        doc.keep(loc, name, "diagnosis")
        doc.keep(loc, code, "diagnosis_code")
        parts.append(f"{name} ({code})")
    return "; ".join(parts)


def _meds(doc: Doc, loc: str, rng: random.Random) -> list[str]:
    lines = []
    for number, (drug, dose, frequency) in enumerate(rng.sample(vocab.DRUGS, 2), 1):
        days = rng.choice((3, 5, 7, 15))
        line = doc.keep(loc, f"{drug} {dose} {frequency} x {days} days", "medication")
        lines.append(f" {number}. {line}")
    return lines


def _doctor_line(doc: Doc, loc: str, ctx: Ctx) -> str:
    doctor = ctx.doctor()
    return doc.keep(loc, f"Dr. {doctor.full}", "doctor") + " (MD Medicine)"


def _address(doc: Doc, loc: str, p: Person, deva: bool = False) -> str:
    house = doc.ent(loc, "HOUSE", p.house if not deva else
                    p.house.replace("Makan No.", "मकान नं.").replace("H.No.", "मकान नं.")
                    .replace("House", "मकान नं."), p, "patient")
    landmark = doc.ent(loc, "LANDMARK", p.landmark.deva if deva else p.landmark.latin, p,
                       "patient")
    village = doc.ent(loc, "VILLAGE", p.village.deva if deva else p.village.latin, p, "patient")
    if deva:
        return f"{house}, {landmark} के पास, ग्राम {village}, जिला {p.district[1]}"
    return f"{house}, near {landmark}, Gram {village}, Distt. {p.district[0]}"


# =========================================================== training layouts


def register_csv(ctx: Ctx, hospital: Hospital, patients: list[Person]) -> Doc:
    doc = Doc(f"registration/{hospital.code.lower()}_patients.csv", "csv", "register",
              "registration", delimiter=",")
    header = [ctx.label("id"), ctx.label("name"), "Sex", ctx.label("age"), ctx.label("dob"),
              "Relation", ctx.label("relative"), ctx.label("address"), ctx.label("village"),
              "District", "PIN", ctx.label("phone"), ctx.label("aadhaar"), "ID Proof"]
    rows = []
    for index, p in enumerate(patients):
        loc = f"row:{index}"
        doc.unit(loc, p)
        number = p.ids[hospital.code]
        rows.append([
            doc.ent(loc, "MRN", number, p, "patient", cue="field"),
            doc.ent(loc, "PATIENT_NAME", p.full.upper() if ctx.rng.random() < 0.15 else p.full,
                    p, "patient", cue="field"),
            p.gender, str(p.age),
            doc.ent(loc, "DOB", ctx.date(p.dob, hospital), p, "patient", cue="field",
                    key=p.dob.isoformat()),
            p.relation,
            doc.ent(loc, "RELATIVE_NAME", p.relative.full, p.relative, "relative", cue="field"),
            _address(doc, loc, p), p.village.latin, p.district[0],
            doc.ent(loc, "PIN", p.pin, p, "patient", cue="field"),
            doc.ent(loc, "PHONE", ctx.phone(p.phone), p, "patient", cue="field"),
            doc.ent(loc, "AADHAAR", ctx.aadhaar(p.aadhaar), p, "patient", cue="field"),
            _id_proof(doc, loc, ctx, p)])
    doc.text = _csv([header] + rows, ",")
    return doc


def _id_proof(doc: Doc, loc: str, ctx: Ctx, p: Person) -> str:
    """Another government ID on the training side: voter ID or ration card (X-09)."""
    if ctx.rng.random() < 0.5:
        return ""
    letters = "".join(ctx.rng.choices("ABCDEFGHJKLMNPRSTUVWXYZ", k=3))
    value = f"Voter ID {letters}{ctx.rng.randint(1000000, 9999999)}" \
        if ctx.rng.random() < 0.5 else f"Ration Card {ctx.rng.randint(10, 99)}{ctx.rng.randint(10**9, 10**10 - 1)}"
    doc.ent(loc, "OTHER_ID", value.split(" ")[-1], p, "patient", cue="field", trap="X-09")
    return value


def discharge_en(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    named = ctx.rng.random() < 0.3
    rel = f"discharge/Discharge_Summary_{p.given.latin}_{p.surname.latin}_{n:04d}.txt" \
        if named else f"discharge/{hospital.code.lower()}_ds_{n:05d}.txt"
    doc = Doc(rel, "text", "discharge_en", "discharge")
    loc = "doc"
    doc.unit(loc, p)
    if named:
        doc.ent(loc, "PATIENT_NAME", p.given.latin, p, "patient", channel="filename",
                cue="file name")
        doc.ent(loc, "PATIENT_NAME", p.surname.latin, p, "patient", channel="filename",
                cue="file name")
    admit = ctx.visit()
    sep = ctx.sep()
    lines = [hospital.name.upper(), "DISCHARGE SUMMARY", "",
             f"{ctx.label('name')}{sep}{doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient')}"
             f"        Age/Sex{sep}{_age_sex(p)}",
             f"{ctx.label('id')}{sep}{doc.ent(loc, 'MRN', number, p, 'patient')}",
             f"{p.relation} {doc.ent(loc, 'RELATIVE_NAME', p.relative.full, p.relative, 'relative', cue='relation')}",
             f"{ctx.label('address')}{sep}{_address(doc, loc, p)} - "
             f"{doc.ent(loc, 'PIN', p.pin, p, 'patient')}",
             f"{ctx.label('phone')}{sep}{doc.ent(loc, 'PHONE', ctx.phone(p.phone), p, 'patient')}",
             f"Consultant{sep}{_doctor_line(doc, loc, ctx)}",
             f"Date of Admission{sep}{ctx.date(admit, hospital)}",
             f"Date of Discharge{sep}{ctx.date(admit + dt.timedelta(days=ctx.rng.randint(2, 9)), hospital)}",
             f"{ctx.label('dob')}{sep}{doc.ent(loc, 'DOB', ctx.date(p.dob, hospital), p, 'patient', key=p.dob.isoformat())}",
             "", f"Diagnosis{sep}{_dx(doc, loc, p)}",
             "Presenting complaints: " + doc.keep(loc, f"C/O fever x {ctx.rng.randint(2, 9)} days",
                                                  "context", trap="N-10"),
             f"Course in hospital: Treated conservatively. "
             f"{doc.ent(loc, 'PATIENT_NAME', p.given.latin, p, 'patient', cue='honorific', hinglish=True)}"
             f"{_after(p)} ki condition ab stable hai.",
             "", "Medications on discharge:", *_meds(doc, loc, ctx.rng), "",
             f"Attendant: {doc.ent(loc, 'RELATIVE_NAME', p.relative.full, p.relative, 'relative')}, "
             f"Mob. {doc.ent(loc, 'PHONE', p.phone, p, 'patient')}"]
    doc.text = "\n".join(lines) + "\n"
    return doc


def _after(p: Person) -> str:
    """The form of address written after the given name, for Hinglish sentences."""
    word, place = p.address_form
    return f" {word}" if word and place == "after" else ""


def discharge_hi(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    doc = Doc(f"discharge/{hospital.code.lower()}_ds_hi_{n:05d}.txt", "text", "discharge_hi",
              "discharge")
    loc = "doc"
    doc.unit(loc, p)
    phone = p.phone.translate(DEVA_DIGITS) if ctx.rng.random() < 0.5 else p.phone
    lines = [hospital.name, "डिस्चार्ज सारांश", "",
             f"{ctx.label('name', deva=True)}: {doc.ent(loc, 'PATIENT_NAME', p.full_deva, p, 'patient')}"
             f"    आयु: {_age_sex(p, deva=True)}",
             f"{ctx.label('relative', deva=True)}: "
             f"{doc.ent(loc, 'RELATIVE_NAME', f'{p.relative.given.deva} {p.relative.surname.deva}', p.relative, 'relative')}",
             f"{ctx.label('id', deva=True)}: {doc.ent(loc, 'MRN', number, p, 'patient')}",
             f"{ctx.label('address', deva=True)}: {_address(doc, loc, p, deva=True)}",
             f"{ctx.label('phone', deva=True)}: {doc.ent(loc, 'PHONE', phone, p, 'patient')}",
             f"भर्ती की तारीख: {ctx.date(ctx.visit(), hospital)}", "",
             f"निदान: {_dx(doc, loc, p)}",
             f"{doc.ent(loc, 'PATIENT_NAME', p.given.deva, p, 'patient', cue='postposition')} को "
             f"{ctx.rng.randint(2, 9)} दिन से बुखार था।", "",
             "Medications on discharge:", *_meds(doc, loc, ctx.rng),
             _doctor_line(doc, loc, ctx) + " reviewed the patient."]
    doc.text = "\n".join(lines) + "\n"
    return doc


def opd_slip(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    doc = Doc(f"prescriptions/rx_{hospital.code.lower()}_{n:05d}.txt", "text", "opd_slip",
              "prescription")
    loc = "doc"
    doc.unit(loc, p)
    code = p.diagnoses[0][1]
    lines = [f"{hospital.name} - OPD Prescription",
             f"Name: {doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient')}   "
             f"Age: {p.age}/{p.gender}   Reg No: {doc.ent(loc, 'MRN', number, p, 'patient')}",
             f"Date: {ctx.date(ctx.visit(), hospital)}",
             f"Mob: {doc.ent(loc, 'PHONE', ctx.phone(p.phone), p, 'patient')}",
             "C/O: " + doc.keep(loc, f"fever x {ctx.rng.randint(2, 9)} days, body ache", "context"),
             "K/C/O: " + doc.keep(loc, code, "diagnosis_code"),
             "Rx", *_meds(doc, loc, ctx.rng),
             "Adv: Low salt diet. Review after 2 wks.", _doctor_line(doc, loc, ctx)]
    doc.text = "\n".join(lines) + "\n"
    return doc


def nursing_note(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    doc = Doc(f"notes/nursing_{n:05d}.txt", "text", "nursing_note", "nursing", hinglish=True)
    loc = "doc"
    doc.unit(loc, p)
    day = ctx.visit()
    variant = _variant(p.given.latin)
    staff = ctx.rng.choice(ctx.world.staff)
    lines = [f"NURSING NOTES - Ward {ctx.rng.randint(1, 9)} (Medicine), Bed "
             f"{ctx.rng.randint(1, 30)} - {doc.ent(loc, 'MRN', number, p, 'patient')}", "",
             f"{ctx.date(day, hospital)} 22:00 - "
             f"{doc.ent(loc, 'PATIENT_NAME', p.given.latin, p, 'patient', cue='honorific')}"
             f"{_after(p)} ka BP 118/80, pulse 96. Bukhar 101 F. "
             + doc.keep(loc, "Paracetamol 650 mg diya.", "medication"),
             "Next morning 06:00 - Patient ne raat theek se neend li.",
             f"14:00 - {doc.ent(loc, 'PATIENT_NAME', f'{variant} {p.surname.latin}', p, 'patient', cue='layout', trap='S-04')}"
             f" - K/C/O {p.diagnoses[0][0].lower()}. Attendant ({p.relation} "
             f"{doc.ent(loc, 'RELATIVE_NAME', p.relative.given.latin, p.relative, 'relative', cue='relation', trap='S-03')})"
             " informed about diet.",
             f"Injection given by {staff.role} "
             f"{doc.ent(loc, 'STAFF_NAME', staff.person.full, staff.person, 'staff', cue='staff role', trap='N-14')}."]
    doc.text = "\n".join(lines) + "\n"
    return doc


def chat_forward(ctx: Ctx, p: Person, n: int) -> Doc:
    doc = Doc(f"lab/whatsapp_forward_{n:04d}.txt", "text", "chat_forward", "lab",
              hinglish=True)
    loc = "doc"
    doc.unit(loc, p)
    day = ctx.visit()
    kin = ctx.rng.choice(["Bhaiya", "Chachu", "Mama", "Didi"])
    hb = round(ctx.rng.uniform(7, 15), 1)
    lines = [f"[{day:%d/%m/%y}, 9:{ctx.rng.randint(10, 59)} pm] "
             f"{doc.ent(loc, 'RELATIVE_NAME', p.relative.given.latin, p.relative, 'relative', cue='chat sender', trap='S-02')} {kin}: "
             "<attachment: report.pdf>", "Forwarded", "*City Pathology*",
             f"Report of {doc.ent(loc, 'PATIENT_NAME', p.given.latin, p, 'patient', cue='report of')}"
             f"{_after(p)}  {p.age} Y / {p.gender}",
             doc.keep(loc, f"Haemoglobin {hb} g/dL", "lab") + " | Random Blood Sugar 140 mg/dL",
             "Dr ne bola " + doc.keep(loc, "Dolo 650 BD x 5 days", "medication", trap="X-01")
             + " lena hai",
             f"Koi problem ho to call karo {doc.ent(loc, 'PHONE', ctx.phone(p.phone), p, 'patient')}"]
    doc.text = "\n".join(lines) + "\n"
    return doc


def lab_csv(ctx: Ctx, hospital: Hospital, patients: list[Person]) -> Doc:
    doc = Doc(f"lab/{hospital.code.lower()}_results.csv", "csv", "lab_csv", "lab",
              delimiter=",")
    header = ["sample_id", ctx.key("name"), "age_sex", ctx.key("id"), "ref_doctor", "test",
              "result", "unit", "collected_on"]
    rows, index = [], 0
    for p in patients:
        for test, unit, (low, high), _ in ctx.rng.sample(vocab.LAB_TESTS, 2):
            loc = f"row:{index}"
            doc.unit(loc, p)
            index += 1
            value = round(ctx.rng.uniform(low, high), 1 if high < 100 else 0)
            name = p.full.upper() if ctx.rng.random() < 0.3 else p.full
            rows.append([f"S{26000000 + index}",
                         doc.ent(loc, "PATIENT_NAME", name, p, "patient", cue="field",
                                 trap="S-15"),
                         f"{p.age}/{p.gender}",
                         doc.ent(loc, "MRN", p.ids[hospital.code], p, "patient", cue="field"),
                         doc.keep(loc, f"Dr. {ctx.doctor().full}", "doctor"), test,
                         str(value), unit, ctx.date(ctx.visit(), hospital)])
    doc.text = _csv([header] + rows, ",")
    return doc


def opd_json(ctx: Ctx, hospital: Hospital, patients: list[Person], n: int) -> Doc:
    doc = Doc(f"opd/{hospital.code.lower()}_opd_{n:02d}.json", "json", "opd_json", "opd",
              hinglish=True)
    first = patients[0]
    visits = []
    meta_name = f"{first.given.latin}_{first.surname.latin}"
    for index, p in enumerate(patients):
        loc = f"/visits/{index}"
        doc.unit(loc, p)
        name, code = p.diagnoses[0]
        doc.keep(loc, name, "diagnosis")
        visits.append({
            "visit_id": f"OPD/26/{n:02d}/{index:05d}",
            ctx.key("id"): doc.ent(loc, "MRN", p.ids[hospital.code], p, "patient", cue="field"),
            "visit_date": ctx.date(ctx.visit(), hospital),
            "doctor": doc.keep(loc, f"Dr. {ctx.doctor().full}", "doctor"),
            "complaints": doc.keep(loc, f"{ctx.rng.randint(2, 9)} din se bukhar", "context"),
            "diagnosis": [{"code": doc.keep(loc, code, "diagnosis_code"), "display": name}],
            "notes": f"{doc.ent(loc, 'PATIENT_NAME', p.given.latin, p, 'patient', cue='honorific', trap='S-01')}"
                     f"{_after(p)} ka BP 130/80, review after 2 weeks.",
            "patient": {ctx.key("name"): doc.ent(loc, "PATIENT_NAME", p.full, p, "patient",
                                                 cue="field"),
                        "age_sex": f"{p.age}/{p.gender}",
                        ctx.key("phone"): doc.ent(loc, "PHONE", ctx.phone(p.phone), p,
                                                  "patient", cue="field")}})
    tree = {"_export_meta": {"system": "eHospital OPD module", "hospital": hospital.name,
                             "source_path": f"D:\\HIS\\exports\\{meta_name}\\visits.json"},
            "visits": visits}
    doc.ent("/visits/0", "PATIENT_NAME", meta_name, first, "patient", channel="metadata",
            cue="path", trap="S-08")
    doc.text = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
    return doc


def lab_json(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    doc = Doc(f"lab/{p.phone}_report_{n:04d}.json", "json", "lab_json", "lab")
    loc = "doc"
    doc.unit(loc, p)
    doc.ent(loc, "PHONE", p.phone, p, "patient", channel="filename", cue="file name",
            trap="H-01")
    results = []
    for test, unit, (low, high), loinc in vocab.LAB_TESTS[:3]:
        value = round(ctx.rng.uniform(low, high), 1)
        results.append({"test": doc.keep(loc, test, "lab"), "loinc": loinc, "value": value,
                        "unit": unit})
    tree = {"lab": "Lifeline Pathology",
            "patient": {"name": doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", cue="field"),
                        "age": p.age, "sex": p.gender,
                        "hospital_uhid": doc.ent(loc, "MRN", number, p, "patient", cue="field")},
            "referred_by": doc.keep(loc, f"Dr. {ctx.doctor().full}", "doctor"),
            "collected": f"{ctx.visit().isoformat()}T09:30:00+05:30", "results": results}
    doc.text = json.dumps(tree, ensure_ascii=False, indent=1) + "\n"
    return doc


def short_note(ctx: Ctx, p: Person, n: int) -> Doc:
    doc = Doc(f"discharge/note_{n:04d}.txt", "text", "short_note", "discharge")
    loc = "doc"
    doc.unit(loc, p, link="none")
    name, _ = p.diagnoses[0]
    lines = ["Discharge note", "",
             f"{doc.ent(loc, 'PATIENT_NAME', p.given.latin, p, 'patient', cue='relation subject')},"
             f" {p.relation} {doc.ent(loc, 'RELATIVE_NAME', p.relative.given.latin, p.relative, 'relative', cue='relation')},"
             " discharged in stable condition.",
             f"Visit: {ctx.visit():%d %B}",
             "Treated for " + doc.keep(loc, name, "diagnosis") + ". " +
             doc.keep(loc, "Continue Amlodipine 5 mg 0-0-1.", "medication")]
    doc.text = "\n".join(lines) + "\n"
    return doc


# =============================================================== test layouts


def referral_slip(ctx: Ctx, p: Person, n: int) -> Doc:
    hospital, number = ctx.hospital_of(p)
    doc = Doc(f"referrals/referral_{n:04d}.txt", "text", "referral_slip", "referral")
    loc = "doc"
    doc.unit(loc, p)
    sep = ctx.sep()
    worker = next((s for s in ctx.world.staff if s.role in ("ASHA worker", "ANM")),
                  ctx.world.staff[0])
    lines = ["REFERRAL SLIP", f"From{sep}PHC {p.district[0]}", f"To{sep}{hospital.name}", "",
             f"{ctx.label('name')}{sep}{doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient')} "
             f"({p.age}/{p.gender})",
             f"{ctx.label('relative')}{sep}"
             f"{doc.ent(loc, 'RELATIVE_NAME', p.relative.full, p.relative, 'relative')}",
             f"{ctx.label('village')}{sep}{doc.ent(loc, 'VILLAGE', p.village.latin, p, 'patient')}",
             f"{ctx.label('phone')}{sep}{doc.ent(loc, 'PHONE', ctx.phone(p.phone), p, 'patient')}",
             f"{ctx.label('id')}{sep}{doc.ent(loc, 'MRN', number, p, 'patient')}",
             "Reason for referral" + sep + doc.keep(loc, p.diagnoses[0][0], "diagnosis"),
             f"Referred by {worker.role} "
             f"{doc.ent(loc, 'STAFF_NAME', worker.person.full, worker.person, 'staff', cue='staff role', trap='N-14')}",
             doc.keep(loc, "ASHA worker to follow up on day 3", "context", trap="N-02")]
    doc.text = "\n".join(lines) + "\n"
    return doc


def handover_note(ctx: Ctx, patients: list[Person], n: int) -> Doc:
    doc = Doc(f"notes/handover_{n:04d}.txt", "text", "handover_note", "nursing",
              hinglish=True)
    loc = "doc"
    doc.unit(loc, None)
    staff = ctx.rng.choice([s for s in ctx.world.staff] or [None])
    lines = [f"Night handover - Ward {ctx.rng.randint(1, 9)}"]
    for index, p in enumerate(patients):
        bed = ctx.rng.randint(1, 30)
        if index % 2 == 0:
            who = doc.ent(loc, "PATIENT_NAME", p.addressed(), p, "patient", cue="layout",
                          trap="N-18")
        else:
            who = doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", cue="layout",
                          trap="N-18")
        lines.append(f"Bed {bed}: {who} ({p.age}/{p.gender}), "
                     + doc.keep(loc, p.diagnoses[0][0], "diagnosis") + ", stable.")
    lines.append(f"Handed over by {staff.role} "
                 f"{doc.ent(loc, 'STAFF_NAME', staff.person.full, staff.person, 'staff', cue='staff role', trap='N-14')}")
    doc.text = "\n".join(lines) + "\n"
    return doc


def mch_card(ctx: Ctx, p: Person, n: int) -> Doc:
    doc = Doc(f"mch/mch_card_{n:04d}.txt", "text", "mch_card", "mch")
    loc = "doc"
    doc.unit(loc, p)
    sep = ctx.sep()
    birth = ctx.visit()
    lines = ["MOTHER AND CHILD PROTECTION CARD",
             f"Mother{sep}{doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient')}, {p.age} yrs",
             f"Husband{sep}{doc.ent(loc, 'RELATIVE_NAME', p.relative.full, p.relative, 'relative')}",
             f"{ctx.label('village')}{sep}{doc.ent(loc, 'VILLAGE', p.village.latin, p, 'patient')}",
             f"Child{sep}B/o {doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient', cue='relation', trap='N-17')}",
             f"Date of birth (child){sep}{ctx.date(birth)}",
             "Birth weight" + sep + doc.keep(loc, "2.8 kg", "context"),
             f"{ctx.label('phone')}{sep}{doc.ent(loc, 'PHONE', ctx.phone(p.phone), p, 'patient')}",
             "Vaccines: " + doc.keep(loc, "BCG, OPV-0, Hep B-1", "medication")]
    doc.text = "\n".join(lines) + "\n"
    return doc


def register_two_header(ctx: Ctx, hospital: Hospital, patients: list[Person]) -> Doc:
    doc = Doc(f"registers/{hospital.code.lower()}_admission_register.csv", "csv",
              "register_two_header", "registration", delimiter=",")
    header = ["S.No", ctx.label("id"), ctx.label("name"), ctx.label("age"),
              ctx.label("relative"), ctx.label("village"), ctx.label("phone"), "Diagnosis"]
    rows = [[f"Admission Register - {hospital.name}"] + [""] * (len(header) - 1), header]
    for index, p in enumerate(patients):
        loc = f"row:{index + 1}"
        doc.unit(loc, p)
        rows.append([str(index + 1),
                     doc.ent(loc, "MRN", p.ids[hospital.code], p, "patient", cue="field"),
                     doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", cue="field"),
                     f"{p.age}/{p.gender}",
                     doc.ent(loc, "RELATIVE_NAME", p.relative.full, p.relative, "relative",
                             cue="field"),
                     doc.ent(loc, "VILLAGE", p.village.latin, p, "patient", cue="field"),
                     doc.ent(loc, "PHONE", ctx.phone(p.phone), p, "patient", cue="field"),
                     doc.keep(loc, p.diagnoses[0][0], "diagnosis")])
    doc.text = _csv(rows, ",")
    return doc


def medico_legal(ctx: Ctx, p: Person, n: int) -> Doc:
    doc = Doc(f"mlc/mlc_{n:04d}.txt", "text", "medico_legal", "mlc")
    loc = "doc"
    doc.unit(loc, p)
    sep = ctx.sep()
    vehicle = f"UP{ctx.rng.randint(10, 80)} {ctx.rng.choice(['AB', 'CK', 'TM'])} " \
              f"{ctx.rng.randint(1000, 9999)}"
    lines = ["MEDICO-LEGAL REGISTER",
             f"MLC No{sep}{doc.ent(loc, 'OTHER_ID', f'MLC/26/{ctx.rng.randint(100, 999)}', p, 'patient', trap='X-09')}",
             f"{ctx.label('name')}{sep}{doc.ent(loc, 'PATIENT_NAME', p.full, p, 'patient')}, "
             f"{p.age}/{p.gender}",
             f"Brought by{sep}{doc.ent(loc, 'RELATIVE_NAME', p.relative.full, p.relative, 'relative', cue='brought by')}",
             f"Police Station{sep}{p.district[0]} Kotwali",
             f"Vehicle{sep}{doc.ent(loc, 'OTHER_ID', vehicle, p, 'patient', trap='X-09')}",
             "Injuries" + sep + doc.keep(loc, "abrasion over left forearm 3 x 2 cm", "context")]
    doc.text = "\n".join(lines) + "\n"
    return doc


def index_json(ctx: Ctx, patients: list[Person], hospital: Hospital) -> Doc:
    doc = Doc(f"misc/{hospital.code.lower()}_index.json", "json", "index_json", "misc")
    loc = "doc"
    doc.unit(loc, None)
    by_name: dict[str, list[str]] = {}
    for p in patients:          # two people can share a name: one key, several numbers
        name = doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", channel="key", cue="key",
                       trap="S-07")
        by_name.setdefault(name, []).append(
            doc.ent(loc, "MRN", p.ids[hospital.code], p, "patient", cue="field"))
    tree = {"generated": "2026-11-19", "by_name": by_name}
    doc.text = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
    return doc


def pharmacy_csv(ctx: Ctx, patients: list[Person], hospital: Hospital) -> Doc:
    doc = Doc(f"pharmacy/{hospital.code.lower()}_dispensing.csv", "csv", "pharmacy_csv",
              "pharmacy", delimiter=",")
    pharmacist = next((s for s in ctx.world.staff if s.role == "Pharmacist"),
                      ctx.world.staff[0])
    rows = [["Date", ctx.label("name"), "Medicine", "Qty", "Dispensed By"]]
    for index, p in enumerate(patients):
        loc = f"row:{index}"
        doc.unit(loc, p, link="none")
        drug, dose, _ = ctx.rng.choice(vocab.DRUGS)
        rows.append([ctx.date(ctx.visit()),
                     doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", cue="field"),
                     doc.keep(loc, f"{drug} {dose}", "medication"), str(ctx.rng.randint(5, 30)),
                     doc.ent(loc, "STAFF_NAME", pharmacist.person.full, pharmacist.person,
                             "staff", cue="field", trap="N-14")])
    doc.text = _csv(rows, ",")
    return doc


def space_table(ctx: Ctx, patients: list[Person], n: int) -> Doc:
    doc = Doc(f"camp/screening_camp_{n:02d}.txt", "text", "space_table", "camp")
    loc = "doc"
    doc.unit(loc, None)
    widths = (28, 6, 22, 14)
    head = [ctx.label("name"), "Age", ctx.label("village"), "Hb"]
    lines = ["HEALTH CAMP SCREENING LIST", "".join(h.ljust(w) for h, w in zip(head, widths, strict=True))]
    for p in patients:
        cells = [doc.ent(loc, "PATIENT_NAME", p.full, p, "patient", cue="layout", trap="L-21"),
                 str(p.age), doc.ent(loc, "VILLAGE", p.village.latin, p, "patient", cue="layout"),
                 doc.keep(loc, f"{round(ctx.rng.uniform(7, 14), 1)} g/dL", "lab")]
        lines.append("".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip())
    doc.text = "\n".join(lines) + "\n"
    return doc


def duty_roster(ctx: Ctx, n: int) -> Doc:
    doc = Doc(f"admin/duty_roster_{n:02d}.txt", "text", "duty_roster", "admin")
    loc = "doc"
    doc.unit(loc, None, link="none")
    lines = [f"DUTY ROSTER - Ward {ctx.rng.randint(1, 9)}", "Shift      Staff"]
    for shift, staff in zip(("Morning", "Evening", "Night"), ctx.world.staff[:3], strict=False):
        lines.append(f"{shift:<10} {staff.role} "
                     f"{doc.ent(loc, 'STAFF_NAME', staff.person.full, staff.person, 'staff', cue='staff role', trap='L-17')}")
    doc.text = "\n".join(lines) + "\n"
    return doc


# =================================================================== canaries


def canary_docs(ctx: Ctx) -> list[Doc]:
    """Four people who appear once, in no list, with little context (docs/TRAPS.md N-05)."""
    w = ctx.world
    docs = []
    witness, caller, visitor, mention = w.canaries
    phone = "9" + "".join(str(ctx.rng.randint(0, 9)) for _ in range(9))

    doc = Doc("misc/scanned_consent_form.json", "json", "canary_base64", "misc")
    doc.unit("doc", None, link="none")
    text = f"Consent witnessed by {witness.full}, phone {phone}."
    doc.ent("doc", "CANARY", witness.full, witness, "witness", channel="base64",
            cue="acted by", trap="S-09")
    doc.ent("doc", "PHONE", phone, witness, "witness", channel="base64", cue="pattern")
    doc.text = json.dumps({"form": "surgical consent", "attachment": {
        "contentType": "text/plain",
        "data": base64.b64encode(text.encode()).decode()}}, indent=2) + "\n"
    docs.append(doc)

    doc = Doc("misc/ambulance_log.csv", "csv", "canary_remark", "misc", delimiter=",")
    doc.unit("row:0", None, link="none")
    remark = f"called by {doc.ent('row:0', 'CANARY', caller.full, caller, 'caller', cue='acted by', trap='S-10')}"
    doc.text = _csv([["trip", "from", "to", "remarks"],
                     ["AMB-410", w.districts[0][0], "Lucknow", remark]], ",")
    docs.append(doc)

    doc = Doc("notes/visitor_log.txt", "text", "canary_visitor", "nursing")
    doc.unit("doc", None, link="none")
    doc.text = (f"VISITOR LOG\n18:40 - "
                f"{doc.ent('doc', 'CANARY', visitor.full, visitor, 'visitor', cue='layout')} "
                f"(visitor, bed {ctx.rng.randint(1, 30)})\n"
                f"19:10 - {doc.keep('doc', 'Dr. ' + ctx.doctor().full, 'doctor')} took evening round.\n")
    docs.append(doc)

    doc = Doc("notes/ward_chat.txt", "text", "canary_chat", "nursing", hinglish=True)
    doc.unit("doc", None, link="none")
    doc.text = (f"Aaj {doc.ent('doc', 'CANARY', mention.full, mention, 'visitor', cue='none', trap='N-05')}"
                " aaye the, kal phir aayenge.\n")
    docs.append(doc)
    return docs


def suits(layout, p: Person) -> bool:
    """A mother-and-child card only for women of child-bearing age."""
    if layout is mch_card:
        return p.gender == "F" and 18 <= p.age <= 45
    return True


def _csv(rows: list[list[str]], delimiter: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


TRAIN = {"single": [discharge_en, discharge_hi, opd_slip, nursing_note, chat_forward,
                    lab_json, short_note],
         "register": register_csv, "lab_table": lab_csv, "json_export": opd_json}
TEST = {"single": [referral_slip, mch_card, medico_legal, discharge_en, nursing_note],
        "register": register_two_header, "multi": [handover_note, space_table],
        "lists": [index_json, pharmacy_csv], "roster": duty_roster}

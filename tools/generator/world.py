"""Invented people, hospitals and staff for one generated dataset."""

from __future__ import annotations

import datetime as dt
import random
import string
from dataclasses import dataclass, field

from generator import vocab

REF_DATE = dt.date(2026, 11, 20)


@dataclass(frozen=True)
class Word:
    latin: str
    deva: str


@dataclass
class Person:
    pid: str
    region: str
    gender: str                       # "F" or "M"
    given: Word
    surname: Word
    age: int = 0
    dob: dt.date = REF_DATE
    phone: str = ""
    aadhaar: str = ""
    address_form: tuple[str, str] = ("", "after")
    relative: "Person | None" = None
    relation: str = ""
    village: Word | None = None
    district: tuple[str, str, str] = ("", "", "")
    pin: str = ""
    house: str = ""
    landmark: Word | None = None
    ids: dict[str, str] = field(default_factory=dict)        # hospital code -> number
    diagnoses: list[tuple[str, str]] = field(default_factory=list)
    traps: list[str] = field(default_factory=list)

    @property
    def full(self) -> str:
        return f"{self.given.latin} {self.surname.latin}"

    @property
    def full_deva(self) -> str:
        return f"{self.given.deva} {self.surname.deva}"

    def addressed(self) -> str:
        """Given name with the region's form of address: "Leela ji", "Pu Lalthanzuala"."""
        word, place = self.address_form
        if not word:
            return self.given.latin
        if place == "before":
            return f"{word} {self.given.latin}"
        if place == "joined":
            return f"{self.given.latin}{word}"
        return f"{self.given.latin} {word}"


@dataclass
class Hospital:
    code: str
    name: str
    district: tuple[str, str, str]
    id_format: str
    us_dates: bool = False
    next_number: int = 1000

    def new_id(self, rng: random.Random) -> str:
        self.next_number += rng.randint(3, 40)
        return self.id_format.format(n=self.next_number)


@dataclass
class Staff:
    role: str
    person: Person


class World:
    """Everything shared by the documents of one dataset."""

    def __init__(self, rng: random.Random, split: str, patients: int) -> None:
        self.rng, self.split = rng, split
        self.regions = vocab.TRAIN_REGIONS if split == "train" else vocab.TEST_REGIONS
        self.districts = vocab.DISTRICTS[split]
        self.hospitals = [self._hospital(i) for i in range(4)]
        self._counter = 0
        self.patients = [self.person(f"PT{i:05d}") for i in range(patients)]
        self._homonyms()
        self.doctors = self._doctors()
        self.staff = [Staff(rng.choice(vocab.STAFF_ROLES), self.person("ST", with_family=False))
                      for _ in range(6)]
        self.canaries = [self.person(f"CN{i}", with_family=False) for i in range(4)]
        for patient in self.patients:
            for hospital in rng.sample(self.hospitals, rng.choice((1, 1, 2))):
                patient.ids[hospital.code] = hospital.new_id(rng)

    # ------------------------------------------------------------------ people

    def person(self, pid: str, with_family: bool = True) -> Person:
        rng = self.rng
        region = rng.choice(self.regions)
        spec = vocab.REGIONS[region]
        gender = rng.choice("FM")
        given = Word(*rng.choice(vocab.pairs(spec["female" if gender == "F" else "male"])))
        surname = Word(*rng.choice(vocab.pairs(spec["surnames"])))
        if region == "punjabi" and rng.random() < 0.5:
            surname = Word("Kaur", "कौर") if gender == "F" else Word("Singh", "सिंह")
        if pid == "ST":
            self._counter += 1
            pid = f"ST{self._counter:03d}"
        person = Person(pid, region, gender, given, surname)
        if not with_family:
            return person
        person.age = rng.choice([rng.randint(1, 15), rng.randint(18, 60), rng.randint(18, 60),
                                 rng.randint(60, 95)])
        person.dob = REF_DATE - dt.timedelta(days=365 * person.age + rng.randint(1, 360))
        person.phone = str(rng.randint(6, 9)) + "".join(rng.choices(string.digits, k=9))
        person.aadhaar = str(rng.randint(2, 9)) + "".join(rng.choices(string.digits, k=11))
        address = [a for a in spec["address"] if a[0] not in ("Pi", "Kong", "Thirumathi")] \
            if gender == "M" else [a for a in spec["address"] if a[0] not in ("Pu", "Bah", "Thiru")]
        person.address_form = rng.choice(address) if address else ("", "after")
        relative = self.person("REL", with_family=False)
        relative.surname = surname if rng.random() < 0.8 else relative.surname
        relative.gender = "M"
        relative.given = Word(*rng.choice([w for w in vocab.pairs(spec["male"])
                                           if w[0] != given.latin]))
        relative.pid = f"{pid}-REL"
        person.relative = relative
        person.relation = self._relation(gender)
        person.district = rng.choice(self.districts)
        person.village = self._village()
        person.pin = person.district[2] + f"{rng.randint(0, 999):03d}"
        person.house = rng.choice(["H.No.", "House", "Makan No."]) + f" {rng.randint(1, 499)}"
        person.landmark = Word(*rng.choice(vocab.landmarks()))
        person.diagnoses = rng.sample(vocab.DIAGNOSES, rng.choice((1, 1, 2)))
        if rng.random() < 0.06:
            person.diagnoses.append(rng.choice(vocab.RARE_DIAGNOSES))
        return person

    def _relation(self, gender: str) -> str:
        options = [r for r, g in vocab.RELATIONS[self.split] if g == gender]
        return self.rng.choice(options)

    def _village(self) -> Word:
        rng = self.rng
        root = Word(*rng.choice(vocab.pairs(vocab.VILLAGE_ROOTS[self.split])))
        if rng.random() < 0.25:
            second = Word(*rng.choice(vocab.pairs(vocab.TWO_WORD_VILLAGE[self.split])))
            ending = Word(*rng.choice(vocab.pairs(vocab.VILLAGE_ENDINGS[self.split])))
            return Word(f"{root.latin}{ending.latin} {second.latin}",
                        f"{root.deva}{ending.deva} {second.deva}")
        ending = Word(*rng.choice(vocab.pairs(vocab.VILLAGE_ENDINGS[self.split])))
        return Word(root.latin + ending.latin, root.deva + ending.deva)

    def _homonyms(self) -> None:
        """Two pairs of different people with the same name (trap N-15)."""
        self.homonym_pairs = []
        for a, b in ((0, 1), (2, 3)):
            if b < len(self.patients):
                first, second = self.patients[a], self.patients[b]
                second.given, second.surname, second.gender = first.given, first.surname, \
                    first.gender
                self.homonym_pairs.append([first.pid, second.pid])

    def _doctors(self) -> list[Person]:
        """Doctors, some sharing a relative's given name (trap N-01)."""
        doctors = []
        for index in range(4):
            doctor = self.person(f"DR{index}", with_family=False)
            doctor.surname = Word(*self.rng.choice(vocab.pairs(vocab.DOCTOR_SURNAMES)))
            if index < 2 and self.patients:
                doctor.given = self.rng.choice(self.patients).relative.given
            doctors.append(doctor)
        return doctors

    # --------------------------------------------------------------- hospitals

    def _hospital(self, index: int) -> Hospital:
        rng = self.rng
        letters = "".join(rng.choices(string.ascii_uppercase, k=rng.choice((2, 3, 4))))
        formats = ["{L}-{{n:07d}}", "{L}/26/{{n:06d}}", "{L}26{{n:05d}}", "{L} {{n:06d}}"]
        name = rng.choice(["Shanti", "Prerna", "Arpan", "Sewa", "Navjeevan", "Kalyan"]) + " " + \
            rng.choice(["Hospital", "Seva Sadan", "Nursing Home", "Medical Centre"])
        district = rng.choice(self.districts)
        return Hospital(letters, f"{name}, {district[0]}", district,
                        rng.choice(formats).format(L=letters), us_dates=index == 3)

    def ids_of(self, person: Person) -> list[tuple[Hospital, str]]:
        return [(h, person.ids[h.code]) for h in self.hospitals if h.code in person.ids]

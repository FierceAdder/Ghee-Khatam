"""Static language resources: gazetteers, stop lists, variants and districts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from wards.errors import ConfigError
from wards.normalize import clean_token, is_devanagari, phonetic_key, transliterate
from wards.textview import TextView

RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources"

# Tokens that appear in many unrelated names and so identify nobody on their own.
GENERIC_NAME_TOKENS = frozenset({
    "devi", "kumar", "kumari", "singh", "kaur", "bano", "begum", "khatun", "mohammad",
    "lal", "prasad", "chand", "bai", "rani", "nath", "ram",
})

HONORIFICS_LATIN = frozenset({
    "ji", "jee", "sahab", "saheb", "sahib", "saab", "sahiba", "didi", "behen", "behenji",
    "bahen", "bahenji", "ben", "bhai", "bhaiya", "bhaiyya", "babu", "amma", "mataji",
    "bhabhi", "chacha", "chachu", "chachi", "mama", "mami", "mausi", "mausa", "nana",
    "nani", "dada", "dadi", "papa", "mummy", "uncle", "aunty", "tau", "tai", "jiju",
    "madam", "dadaji", "dadiji", "nanaji", "naniji", "chachaji", "pitaji", "akka", "anna",
    "garu", "paaji", "paji", "bhaji", "beji", "bibi", "begum", "khala", "khalu", "apa",
    "baji", "boudi", "mashi", "kaku",
})
HONORIFICS_DEVANAGARI = (
    "जी", "साहब", "साहिब", "दीदी", "बहन", "बहनजी", "भाई", "भैया", "बाबू", "अम्मा", "माताजी",
    "भाभी", "चाचा", "चाची", "मामा", "मामी", "मौसी", "नाना", "नानी", "दादा", "दादी", "पापा",
)
# Kinship words: a WhatsApp sender saved as "Temjen Bhaiya" is a relative.
KINSHIP = frozenset({
    "bhai", "bhaiya", "bhaiyya", "bhabhi", "chacha", "chachu", "chachi", "mama", "mami",
    "mausi", "mausa", "nana", "nani", "dada", "dadi", "papa", "mummy", "uncle", "aunty",
    "tau", "tai", "jiju", "behen", "bahen", "didi",
})

# Jobs in a hospital. On their own, or beside a department word, they name nobody: a role
# is never a patient or a relative.
ROLE_WORDS = frozenset({
    "assistant", "assistants", "asst", "attendant", "attendants", "boy", "wardboy", "aaya",
    "helper", "orderly", "staff", "sister", "sisters", "nurse", "nurses",
    "technician", "technicians", "technologist", "tech", "pharmacist", "compounder",
    "kampounder", "dresser", "sweeper", "peon", "chaprasi", "receptionist", "counsellor",
    "counselor", "operator", "phlebotomist", "radiographer", "paramedic", "midwife",
    "supervisor", "incharge", "intern", "interns", "karmachari", "karamchari",
    "karmchari", "paricharak", "paricharika",
    "कर्मचारी", "परिचारक", "परिचारिका", "कंपाउंडर", "कम्पाउंडर", "चपरासी", "बॉय",
    "वार्डबॉय", "टेक्नीशियन", "तकनीशियन", "असिस्टेंट", "अटेंडेंट", "स्टाफ", "सिस्टर", "नर्स",
    "फार्मासिस्ट",
})
# Words that say where such a job is done ("Lab Assistant", "ward boy").
DEPARTMENT_WORDS = frozenset({
    "lab", "laboratory", "ward", "office", "hospital", "nursing", "duty", "head", "night",
    "day", "ot", "icu", "nicu", "opd", "ipd", "xray", "pharmacy",
    "लैब", "वार्ड", "ड्यूटी", "अस्पताल",
})


def loose_spelling(latin: str) -> str:
    """Spelling with the vowels Hindi writing leaves unstable removed.

    Transliteration keeps silent medial vowels (सुखविंदर -> "sukhavindar"), so Devanagari
    words are compared with gazetteer names after dropping non-initial "a"/"e". This is
    far stricter than the phonetic key, which would take ordinary Hindi words for names.
    """
    text = latin.replace("aa", "a").replace("ee", "i").replace("oo", "u")
    for old, new in (("ngh", "nh"), ("ph", "f"), ("w", "v"), ("qu", "q"), ("q", "k")):
        text = text.replace(old, new)
    if text.endswith("y"):
        text = text[:-1] + "i"
    body = text[:1] + re.sub(r"[ae]", "", text[1:])
    return re.sub(r"(.)\1+", r"\1", body)


def _view_form(word: str) -> str:
    """Resource words are stored in the same normalised form the detectors see."""
    return TextView.of(word).text


def _lines(path: Path):
    if not path.is_file():
        raise ConfigError(f"resource_missing.{path.stem}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            yield line


def _words(path: Path) -> frozenset[str]:
    return frozenset(_view_form(word).lower() for line in _lines(path) for word in line.split())


@dataclass(frozen=True)
class District:
    latin: str
    devanagari: str


@dataclass(eq=False)
class Lexicon:
    first_names: frozenset[str] = frozenset()
    surnames: frozenset[str] = frozenset()
    stopwords: frozenset[str] = frozenset()
    medical: frozenset[str] = frozenset()
    variants: dict[str, str] = field(default_factory=dict)
    districts: dict[str, District] = field(default_factory=dict)
    first_spellings: frozenset[str] = frozenset()
    surname_spellings: frozenset[str] = frozenset()
    _canonical: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def load(cls, directory: Path = RESOURCE_DIR) -> "Lexicon":
        first = _words(directory / "first_names.txt")
        last = _words(directory / "surnames.txt")
        variants = {}
        for line in _lines(directory / "name_variants.tsv"):
            parts = line.split("\t")
            if len(parts) == 2:
                variants[parts[0].strip().lower()] = parts[1].strip().lower()
        districts: dict[str, District] = {}
        for line in _lines(directory / "districts.tsv"):
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 2:
                continue
            entry = District(latin=parts[0], devanagari=parts[1])
            spellings = [parts[0], parts[1]]
            if len(parts) > 2:
                spellings += parts[2].split("|")
            for spelling in spellings:
                districts[_district_key(_view_form(spelling))] = entry
        return cls(
            first_names=first,
            surnames=last,
            stopwords=_words(directory / "stopwords.txt"),
            medical=_words(directory / "medical_lexicon.txt"),
            variants=variants,
            districts=districts,
            first_spellings=frozenset(map(loose_spelling, first)),
            surname_spellings=frozenset(map(loose_spelling, last)),
        )

    # ------------------------------------------------------------------ names

    def canonical(self, token: str) -> str:
        """Latin, lower-case, variant-folded form of a name token in either script."""
        cached = self._canonical.get(token)
        if cached is None:
            word = clean_token(token)
            if is_devanagari(word):
                word = transliterate(word)
            cached = self._canonical[token] = self.variants.get(word, word)
        return cached

    def name_key(self, token: str) -> str:
        return phonetic_key(self.canonical(token))

    def is_stopword(self, token: str) -> bool:
        word = clean_token(token)
        if not word:
            return True
        if word in self.stopwords or word in self.medical:
            return True
        # possessives: "Imli's" is judged as "Imli"
        if word.endswith("s") and token.rstrip(".").lower().endswith(("'s", "’s")):
            stem = word[:-1]
            return stem in self.stopwords or stem in self.medical
        return False

    def is_medical(self, token: str) -> bool:
        """A medicine or condition word (which may also be somebody's name)."""
        return clean_token(token) in self.medical

    def is_first_name(self, token: str) -> bool:
        word = self.canonical(token)
        if word in self.first_names:
            return True
        return self._spelled_like(token, word, self.first_spellings)

    def is_surname(self, token: str) -> bool:
        word = self.canonical(token)
        if word in self.surnames:
            return True
        return self._spelled_like(token, word, self.surname_spellings)

    @staticmethod
    def _spelled_like(token: str, word: str, spellings: frozenset[str]) -> bool:
        if not is_devanagari(token):
            return False
        loose = loose_spelling(word)
        return len(loose) >= 3 and loose in spellings

    def is_known_name(self, token: str) -> bool:
        return self.is_first_name(token) or self.is_surname(token)

    def is_generic(self, token: str) -> bool:
        return self.canonical(token) in GENERIC_NAME_TOKENS

    # ------------------------------------------------------------------ places

    def district(self, text: str) -> District | None:
        return self.districts.get(_district_key(text))


def _district_key(text: str) -> str:
    return "".join(ch for ch in text.lower() if not ch.isspace() and ch not in ".-,।")

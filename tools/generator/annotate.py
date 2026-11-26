"""A generated document together with its answer key, in the toolkit's truth format."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

from generator.world import Person

DIGIT_TYPES = {"PHONE", "AADHAAR", "ABHA_NUMBER", "MRN", "OTHER_ID"}
NAME_TYPES = {"PATIENT_NAME", "RELATIVE_NAME", "STAFF_NAME", "CANARY"}
# Words that identify nobody on their own (as the scorer treats Devi, Kumar, Md., ji).
GENERIC = frozenset("""
ji sahab saab bhai ben bhaiya didi di da dada amma babu begum khala paaji bibi garu anna akka
tai rao pu pi bah kong thiru thirumathi devi kumar kumari singh kaur md mohd mohammad smt shri
""".split())
_WORDS = re.compile(r"[A-Za-z]+|[ऀ-ॣॱ-ॿ]+")
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def norm(text: str) -> str:
    """The toolkit's normalisation (check_dev.py)."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def with_b64(text: str) -> str:
    extra = []
    for m in _B64.finditer(text):
        token = m.group(0)
        try:
            extra.append(base64.b64decode(token + "=" * (-len(token) % 4))
                         .decode("utf-8", errors="ignore"))
        except (binascii.Error, ValueError):
            pass
    return text + "\n" + "\n".join(extra) if extra else text


def script_of(surface: str, hinglish: bool) -> str:
    if any("ऀ" <= c <= "ॿ" for c in surface):
        return "deva"
    return "hinglish" if hinglish else "latin"


def name_tokens(surface: str) -> list[str]:
    words = [w for w in _WORDS.findall(surface)]
    # a joined form of address ("Bhikhubhai") is a name with "bhai" on the end
    out = []
    for word in words:
        low = norm(word)
        for suffix in ("bhai", "ben"):
            if low.endswith(suffix) and len(low) > len(suffix) + 2:
                low = low[:-len(suffix)]
        if low not in GENERIC and len(low) > 1:
            out.append(low)
    return out


@dataclass
class Unit:
    loc: str
    patient: str | None
    link: str
    entities: list[dict] = field(default_factory=list)
    preserve: list[dict] = field(default_factory=list)


@dataclass
class Doc:
    rel: str
    fmt: str                          # csv | tsv | json | text
    layout: str
    category: str
    encoding: str = "utf-8"
    delimiter: str | None = None
    hinglish: bool = False
    text: str = ""
    units: dict[str, Unit] = field(default_factory=dict)

    def unit(self, loc: str, patient: Person | None, link: str | None = None) -> Unit:
        """A record; ``link`` defaults to "required" for one patient, "ambiguous" for several."""
        if loc not in self.units:
            if link is None:
                link = "required" if patient else "ambiguous"
            self.units[loc] = Unit(loc, patient.pid if patient else None, link)
        return self.units[loc]

    def ent(self, loc: str, etype: str, surface: str, person: Person | str, role: str,
            channel: str = "content", cue: str = "label", trap: str = "",
            hinglish: bool | None = None, key: str | None = None) -> str:
        """Record one piece of personal data; returns ``surface`` for inline use."""
        pid = person if isinstance(person, str) else person.pid
        entity = {"type": etype, "surface": surface, "person": pid, "role": role,
                  "script": script_of(surface, self.hinglish if hinglish is None else hinglish),
                  "channel": channel, "cue": cue}
        if trap:
            entity["trap"] = trap
        if etype in NAME_TYPES:
            entity.update(match="name", tokens=name_tokens(surface),
                          full=len(name_tokens(surface)) >= 2)
        elif etype in DIGIT_TYPES:
            digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", surface))
            runs = re.findall(r"\d{4,}", unicodedata.normalize("NFKC", surface))
            entity.update(match="digits", key=(runs[-1] if etype == "MRN" and runs else digits))
        elif etype == "DOB":
            entity.update(match="dob", key=key)      # the true date, ISO
        else:
            entity.update(match="text", key=norm(surface))
        self.units[loc].entities.append(entity)
        return surface

    def keep(self, loc: str, text: str, kind: str, trap: str = "") -> str:
        probe = {"text": text, "kind": kind}
        if trap:
            probe["trap"] = trap
        self.units[loc].preserve.append(probe)
        return text

    # ----------------------------------------------------------------- checks

    def missing(self) -> list[dict]:
        """Answer-key entries whose text is not in the file (should be none)."""
        haystack = norm(with_b64(self.text))
        name = norm(self.rel)
        out = []
        for unit in self.units.values():
            for entity in unit.entities:
                where = name if entity["channel"] == "filename" else haystack
                if norm(entity["surface"]) not in where:
                    out.append(entity)
            for probe in unit.preserve:
                if norm(probe["text"]) not in haystack:
                    out.append(probe)
        return out

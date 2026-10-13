"""Recognise European legal acts and their market nicknames.

Practitioners ask about "EMIR" or "MiFID II", but the texts call themselves
"le présent règlement". Without the nickname, neither the embedding nor the
full-text index can tie such a question to the right document. The official
reference is read from the first page and mapped to the usual name; both are then
prepended to every chunk heading of the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Official number -> usual name(s). Financial-services acts most often found in
# French compliance corpora.
KNOWN_ACTS: dict[str, str] = {
    "2014/65": "MiFID II",
    "600/2014": "MiFIR",
    "648/2012": "EMIR",
    "2019/834": "EMIR Refit",
    "2022/2554": "DORA",
    "2019/2088": "SFDR",
    "2020/852": "Taxonomie",
    "2022/2464": "CSRD",
    "596/2014": "MAR (abus de marché)",
    "2014/57": "MAD II",
    "1286/2014": "PRIIPs",
    "2009/65": "OPCVM (UCITS)",
    "2011/61": "AIFM",
    "2015/849": "LCB-FT (4e directive anti-blanchiment)",
    "2018/843": "LCB-FT (5e directive anti-blanchiment)",
    "2024/1624": "AMLR (règlement anti-blanchiment)",
    "2016/679": "RGPD",
    "2017/1129": "Prospectus",
    "575/2013": "CRR",
    "2013/36": "CRD IV",
    "2019/876": "CRR II",
    "2023/1114": "MiCA",
    "909/2014": "CSDR",
    "2015/2366": "DSP2",
    "2016/97": "DDA (distribution d'assurances)",
    "2009/138": "Solvabilité II",
    "2016/1011": "Benchmarks (BMR)",
    "2015/2365": "SFTR",
    "2017/2402": "Titrisation (STS)",
    "2024/1689": "AI Act",
}

# Case-sensitive on purpose: official titles are upper-case and open the document, so
# an internal procedure that merely cites "la directive 2014/65/UE" is not labelled.
_ACT = re.compile(
    r"\b(?P<kind>DIRECTIVE|R[ÈE]GLEMENT(?:\s+D[ÉE]L[ÉE]GU[ÉE]|\s+D['’]EX[ÉE]CUTION)?|D[ÉE]CISION)"
    r"\s*(?:\((?P<family>UE|CE|CEE|Euratom)\))?"
    r"\s*(?:N\s*[°o]\s*)?"
    r"(?P<number>\d{2,4}\s*/\s*\d{1,4})(?:\s*/\s*(?P<suffix>UE|CE|CEE))?",
)


@dataclass(frozen=True)
class ActReference:
    official: str  # e.g. "Directive 2014/65/UE"
    alias: str | None  # e.g. "MiFID II"

    @property
    def label(self) -> str:
        return f"{self.alias} — {self.official}" if self.alias else self.official


def _kind_label(kind: str) -> str:
    normalised = " ".join(kind.split()).lower()
    if normalised.startswith("directive"):
        return "Directive"
    if normalised.startswith("d"):
        return "Décision"
    if "délégué" in normalised or "delegue" in normalised:
        return "Règlement délégué"
    if "exécution" in normalised or "execution" in normalised:
        return "Règlement d'exécution"
    return "Règlement"


def detect_act(first_page_text: str) -> ActReference | None:
    """Find the act's own reference in the opening of its first page."""
    head = " ".join(first_page_text.split())[:600]
    match = _ACT.search(head)
    if match is None:
        return None
    kind = _kind_label(match.group("kind"))
    number = re.sub(r"\s+", "", match.group("number"))
    family = match.group("family")
    suffix = match.group("suffix")
    if kind == "Directive":
        official = f"Directive {number}/{suffix or family or 'UE'}"
    else:
        official = f"{kind} ({family or 'UE'}) n° {number}"
    return ActReference(official=official, alias=KNOWN_ACTS.get(number))

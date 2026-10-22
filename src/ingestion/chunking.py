"""Structure-aware chunking.

Regulatory texts are organised as Partie / Titre / Chapitre / Section / Article. A
chunk that straddles two articles mixes two legal provisions, and a fixed-size
splitter does that constantly. Here the text is first cut at heading boundaries;
only sections longer than ``chunk_size`` are split further. Each chunk records the
page it starts on and its heading path, which is also prepended to the indexed
text so that both the embedding and the full-text index "know" which article a
passage belongs to.

Definition sections ("Article 2 — Définitions", a glossary…) are split one
definition per chunk: a list of thirty definitions cut every 1 200 characters buries
each term among unrelated ones, and the passage holding "contrepartie financière"
never ranks. Each definition chunk repeats the introductory sentence ("Aux fins du
présent règlement, on entend par :") and carries the term in its heading path.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.ingestion.loaders import PageText

# (level, compiled pattern). Lower level = higher in the hierarchy.
_TAIL = r"(?:\s*[-–—:.]\s*(?P<title>.{1,120}))?\s*$"
_ORDINAL = r"(?i:premi[eè]re?|1er|unique|pr[ée]liminaire)"
_ROMAN_OR_NUM = r"(?:[IVXLCDM]+|\d+)"
_HEADINGS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(rf"^(?P<kind>PARTIE|Partie|PART|Part)\s+(?P<num>{_ROMAN_OR_NUM}|{_ORDINAL})" + _TAIL)),
    (1, re.compile(rf"^(?P<kind>ANNEXE|Annexe|ANNEX|Annex)(?:\s+(?P<num>{_ROMAN_OR_NUM}|[A-Z]))?" + _TAIL)),
    (2, re.compile(rf"^(?P<kind>TITRE|Titre|TITLE|Title)\s+(?P<num>{_ROMAN_OR_NUM}|{_ORDINAL})" + _TAIL)),
    (3, re.compile(rf"^(?P<kind>CHAPITRE|Chapitre|CHAPTER|Chapter)\s+(?P<num>{_ROMAN_OR_NUM}|{_ORDINAL})" + _TAIL)),
    (4, re.compile(rf"^(?P<kind>SECTION|Section)\s+(?P<num>{_ROMAN_OR_NUM}|[A-Z]|{_ORDINAL})" + _TAIL)),
    (5, re.compile(rf"^(?P<kind>SOUS-SECTION|Sous-section)\s+(?P<num>{_ROMAN_OR_NUM}|[A-Z])" + _TAIL)),
    (6, re.compile(r"^(?P<kind>Article|ARTICLE)\s+(?P<num>\d+(?:\s?(?:bis|ter|quater|[a-z]))?|premier|1er)" + _TAIL)),
]
_MARKDOWN = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.{1,200})$")

_DEFINITIONS_TITLE = re.compile(r"d[ée]finitions?|glossaire|lexique|terminologie|vocabulaire", re.IGNORECASE)
_DEFINITIONS_INTRO = re.compile(r"on entend par|au sens d[eu]|signifient?\s*:|désigne(?:nt)?\s*:|means\s*:", re.IGNORECASE)
# "8) «contrepartie financière», une entreprise…" / "12. « Salarié » : …" — not "7), de ladite".
_NUMBERED_ITEM = re.compile(r"^(?P<num>\d{1,3})\s*[).]\s+(?P<rest>\S.*)$")
# "- **Astreinte** : période…" / "• Forfait jours : …"
_BULLET_ITEM = re.compile(r"^[-•*]\s+(?P<rest>\S.*)$")
_TERM = re.compile(r"^(?:\*\*(?P<bold>[^*]{1,80})\*\*|[«\"“]\s*(?P<quoted>[^»\"”]{1,80}?)\s*[»\"”]|(?P<plain>[^:,;]{1,60})\s*:)")


def detect_heading(line: str) -> tuple[int, str, bool] | None:
    """Return (level, label, expects_title) if the line is a structural heading.

    ``expects_title`` is true for legal headings without an inline title: EU texts put
    the title ("Transposition", "Agrément") on the following line.
    """
    if not line or len(line) > 160:
        return None
    md = _MARKDOWN.match(line)
    if md:
        return len(md.group("hashes")), md.group("title").strip(), False
    for level, pattern in _HEADINGS:
        match = pattern.match(line)
        if match:
            kind = match.group("kind").capitalize()
            num = (match.groupdict().get("num") or "").strip()
            if num.isalpha() and not re.fullmatch(r"[IVXLCDM]+|[A-Z]", num):
                num = num.lower()
            label = f"{kind} {num}".strip()
            title = (match.groupdict().get("title") or "").strip()
            if title:
                label = f"{label} — {title}"
            return level, label, not title
    return None


def is_meaningful(text: str, min_alpha_ratio: float = 0.4, min_length: int = 20) -> bool:
    """Reject chunks that are too short or mostly non-alphabetic (tables of figures, 2D-DOC noise)."""
    text = text.strip()
    if len(text) < min_length:
        return False
    alpha = sum(c.isalpha() for c in text)
    return (alpha / len(text)) >= min_alpha_ratio


@dataclass
class Chunk:
    content: str  # indexed text: heading path + body
    body: str
    page: int | None
    section: str | None
    section_path: list[str] = field(default_factory=list)


@dataclass
class _Section:
    path: list[str]
    level: int | None = None
    lines: list[tuple[str, int | None]] = field(default_factory=list)
    # Lines that are neither the heading nor its title. A section without any
    # (e.g. "TITRE VII" immediately followed by "CHAPITRE I") yields no chunk.
    body_lines: int = 0


def _sections(pages: list[PageText]) -> list[_Section]:
    sections: list[_Section] = []
    path: dict[int, str] = {}
    current = _Section(path=[])
    awaiting_title = False

    for page in pages:
        for raw in page.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            heading = detect_heading(line)
            if heading:
                if current.lines:
                    sections.append(current)
                level, label, awaiting_title = heading
                path = {lvl: lab for lvl, lab in path.items() if lvl < level}
                path[level] = label
                current = _Section(path=[path[k] for k in sorted(path)], level=level, lines=[(line, page.number)])
                continue
            if awaiting_title:
                awaiting_title = False
                if len(line) <= 120 and not line.endswith((".", ";", ":", ",")) and current.level is not None:
                    titled = f"{current.path[-1]} — {line}"
                    current.path[-1] = titled
                    path[current.level] = titled
                    current.lines.append((line, page.number))
                    continue
            current.lines.append((line, page.number))
            current.body_lines += 1
    if current.lines:
        sections.append(current)
    return sections


Lines = list[tuple[str, int | None]]


def _term_label(text: str) -> str | None:
    match = _TERM.match(text)
    if not match:
        return None
    term = (match.group("bold") or match.group("quoted") or match.group("plain") or "").strip(" *«»\"“”")
    return term or None


def definition_items(section: _Section) -> tuple[Lines, list[tuple[str, Lines]]] | None:
    """(introductory lines, [(label, lines)]) when ``section`` is a list of definitions.

    An item names its term («…», **…** or "Terme :") and is numbered "1)", "2)"…
    (numbering starts at 1 and increases by one, so neither the paragraph number
    "1. Aux fins de…" nor a wrapped "7), de ladite directive" starts an item) or
    bulleted "- **Terme** : …". Sub-items ("a)", "i)") stay with their definition. ``None`` when
    the section is not a definitions list or holds fewer than two definitions.
    """
    title = section.path[-1] if section.path else ""
    body = list(section.lines)
    if section.level is not None and body:
        body = body[1:]  # the heading line itself
        if body and title.endswith(f" — {body[0][0]}"):
            body = body[1:]  # the title that followed it ("Définitions")
    opening = " ".join(line for line, _ in body[:4])
    if not (_DEFINITIONS_TITLE.search(title) or _DEFINITIONS_INTRO.search(opening)):
        return None

    intro: Lines = []
    items: list[tuple[str, Lines]] = []
    mode: str | None = None
    expected = 1
    for line, page in body:
        numbered = _NUMBERED_ITEM.match(line)
        bullet = _BULLET_ITEM.match(line)
        label: str | None = None
        term = _term_label(numbered.group("rest")) if numbered else None
        if term and mode in (None, "numbered") and int(numbered.group("num")) == expected:
            mode, expected = "numbered", expected + 1
            label = f"{numbered.group('num')}) {term}"
        elif bullet and mode in (None, "bullet") and _term_label(bullet.group("rest")):
            mode = "bullet"
            label = _term_label(bullet.group("rest"))
        if label:
            items.append((label, [(line, page)]))
        elif items:
            items[-1][1].append((line, page))
        else:
            intro.append((line, page))
    return (intro, items) if len(items) >= 2 else None


def _emit(
    lines: Lines,
    path: list[str],
    splitter: RecursiveCharacterTextSplitter,
    chunk_overlap: int,
    doc_label: str | None,
) -> list[Chunk]:
    offsets: list[int] = []
    page_numbers: list[int | None] = []
    parts: list[str] = []
    cursor = 0
    for line, page_number in lines:
        offsets.append(cursor)
        page_numbers.append(page_number)
        parts.append(line)
        cursor += len(line) + 1
    text = "\n".join(parts)

    chunks: list[Chunk] = []
    search_from = 0
    for piece in splitter.split_text(text):
        position = text.find(piece, max(0, search_from - chunk_overlap))
        if position < 0:
            position = search_from
        search_from = position + len(piece)
        if not is_meaningful(piece):
            continue
        page = page_numbers[max(0, bisect.bisect_right(offsets, position) - 1)]
        header = " › ".join(([doc_label] if doc_label else []) + path)
        content = f"[{header}]\n{piece}" if header else piece
        chunks.append(
            Chunk(
                content=content,
                body=piece,
                page=page,
                section=path[-1] if path else None,
                section_path=list(path),
            )
        )
    return chunks


def chunk_pages(
    pages: list[PageText],
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
    doc_label: str | None = None,
    split_definitions: bool = True,
) -> list[Chunk]:
    """``doc_label`` (e.g. "MiFID II — Directive 2014/65/UE") heads every chunk's path."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks: list[Chunk] = []
    for section in _sections(pages):
        if section.body_lines == 0:
            continue
        definitions = definition_items(section) if split_definitions else None
        if definitions is None:
            chunks.extend(_emit(section.lines, section.path, splitter, chunk_overlap, doc_label))
            continue
        intro, items = definitions
        for label, item_lines in items:
            # The introduction is repeated in every definition chunk, at the item's page.
            item_page = item_lines[0][1]
            lines = [(line, item_page) for line, _ in intro] + item_lines
            chunks.extend(_emit(lines, [*section.path, label], splitter, chunk_overlap, doc_label))
    return chunks

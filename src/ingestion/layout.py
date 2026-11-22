"""Page layout facts that hold for any paginated document.

Running headers and footers — the document's title or reference, the chapter, the page
number — repeat at the top or bottom of most pages. They say what the document is about,
and inside passages they are noise.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from src.ingestion.chunking import detect_heading
from src.ingestion.loaders import PageText
from src.ingestion.references import extract_references
from src.ocr.reader import text_quality

_EDGE_LINES = 3  # lines looked at, at the top and at the bottom of each page
_DIGITS = re.compile(r"\d+")


def _normalise(line: str) -> str:
    return " ".join(_DIGITS.sub("#", line.lower()).split()).strip(" .-–—|·•")


def running_lines(pages: Sequence[PageText], min_share: float = 0.4, min_pages: int = 3) -> list[str]:
    """Lines found at the top or bottom of at least ``min_share`` of the pages with text
    (and at least ``min_pages``), digits ignored ("Page 3", "Page 4" are one line); the most
    frequent spelling of each, most repeated first. Empty for unpaginated text."""
    texts = [page.text for page in pages if page.number is not None and page.text.strip()]
    if len(texts) < min_pages:
        return []
    pages_with: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for text in texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        edges = lines[:_EDGE_LINES] + lines[-_EDGE_LINES:]
        seen: set[str] = set()
        for line in edges:
            key = _normalise(line)
            # A heading is structure, not a running line, even when every page starts with
            # one ("Article 93", "Article 94"… are one line once digits are ignored).
            if not key or key in seen or len(line) > 160 or detect_heading(line, markdown=False):
                continue
            seen.add(key)
            pages_with[key] += 1
            spellings[key][line] += 1
    needed = max(min_pages, math.ceil(min_share * len(texts)))
    return [spellings[key].most_common(1)[0][0] for key, count in pages_with.most_common() if count >= needed]


def strip_running_lines(pages: Sequence[PageText], running: Sequence[str]) -> list[PageText]:
    """``pages`` without their running headers and footers (looked for at the page edges only)."""
    keys = {_normalise(line) for line in running}
    if not keys:
        return list(pages)
    stripped = []
    for page in pages:
        lines = page.text.splitlines()
        filled = [i for i, line in enumerate(lines) if line.strip()]
        edges = set(filled[:_EDGE_LINES] + filled[-_EDGE_LINES:])
        kept = [line for i, line in enumerate(lines) if i not in edges or _normalise(line) not in keys]
        stripped.append(PageText(page.number, "\n".join(kept)))
    return stripped


_MAX_TITLE = 120


def _titled(line: str) -> bool:
    words = [w for w in line.split() if any(ch.isalpha() for ch in w)]
    quality = text_quality(line)
    return len(words) >= 2 and len(line) <= _MAX_TITLE and quality is not None and quality >= 0.6


def document_title(pages: Sequence[PageText], running: Sequence[str], metadata_title: str | None = None) -> str | None:
    """A title for a paginated document, to head its passages (never shown as a citation).

    In order: the PDF's own title when it reads as one ("NI_US10001_2" does not); a running
    header or footer present on most pages ("US30010/15/20/30/40CNG Manuel d'utilisation");
    the first lines of the first page with text (documents of two pages or more, up to the
    first structural heading). ``None`` for unpaginated text, whose headings already make
    the section path.
    """
    texts = [page for page in pages if page.number is not None and page.text.strip()]
    if not texts:
        return None
    if metadata_title and " " in metadata_title.strip() and "_" not in metadata_title and _titled(metadata_title):
        return " ".join(metadata_title.split())[:_MAX_TITLE]
    # A running line naming a product reference says the most about the document.
    for line in sorted(running, key=lambda line: not extract_references(line)):
        key = _normalise(line)
        on_pages = sum(key in {_normalise(x) for x in page.text.splitlines()} for page in texts)
        if _titled(line) and on_pages >= 0.6 * len(texts):
            return " ".join(line.split())
    if len(texts) < 2:
        return None  # a one-page note: its first line is its content, not a cover
    parts: list[str] = []
    for line in (line.strip() for line in texts[0].text.splitlines()):
        if not line:
            continue
        if detect_heading(line, markdown=False):
            break  # structured from the first line on: the section path says it all
        if len(parts) >= 3 or len(" — ".join([*parts, line])) > _MAX_TITLE:
            break
        quality = text_quality(line)
        if quality is None or quality < 0.6:
            continue
        parts.append(" ".join(line.split()))
        joined = " ".join(parts)
        if _titled(joined) and (len(joined.split()) >= 3 or extract_references(joined)):
            break
    title = " — ".join(parts)
    return title if _titled(title) else None

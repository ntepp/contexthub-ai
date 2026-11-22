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

from src.ingestion.loaders import PageText

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
            if not key or key in seen or len(line) > 160:
                continue
            seen.add(key)
            pages_with[key] += 1
            spellings[key][line] += 1
    needed = max(min_pages, math.ceil(min_share * len(texts)))
    return [spellings[key].most_common(1)[0][0] for key, count in pages_with.most_common() if count >= needed]

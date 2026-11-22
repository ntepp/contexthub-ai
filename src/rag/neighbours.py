"""Neighbours in nearby sections (``RETRIEVAL_NEIGHBOUR_SCOPE=chapter``).

A procedure often uses what the section before or after it describes: the keys of a control
panel are listed in one section, the screen they drive in the next one; an article refers to
the definition given just above it. With the ``chapter`` scope, after its own-section
neighbours (:func:`src.rag.retrieval.expand`), each kept passage's run of passages is carried
on, on each side, up to the first passage of the **previous / next section of the same
chapter** — a section that shares its parent with the passage's own section
(:func:`near_sections`).

The walk is short and contiguous: at most ``RETRIEVAL_NEIGHBOURS + 1`` more passages on each
side, never skipping one, pages next to each other along the way; the passages of the own
section it crosses to get there come with it. A walk that does not reach a nearby section
(the section goes on, an unrelated chapter starts, a page jump, the budget) adds nothing.

Budget (``RETRIEVAL_CONTEXT_CHARS``) priority: kept passages are served best-ranked first; for
each one, its own-section neighbours, then its walk to the nearby sections, then the next kept
passage. Serving the own-section neighbours of every kept passage before any nearby section
was measured to leave no budget for nearby sections with the default ``RETRIEVAL_TOP_K`` and
``RETRIEVAL_CONTEXT_CHARS`` (docs/evaluation.md): what lies next to the best passage is worth
more than what lies next to the eighth.

Like every neighbour, these passages only come with a kept passage: they never decide whether
the question is answered and never count in the confidence.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence

from src.retrieval.store import RetrievedChunk

SECTION = "section"
CHAPTER = "chapter"

Position = tuple[uuid.UUID, int]


def section_path(chunk: RetrievedChunk) -> list[str]:
    return list((chunk.metadata or {}).get("section_path") or [])


def near_sections(own: Sequence[str], other: Sequence[str]) -> bool:
    """Whether ``other`` is the section before / after ``own`` in the same chapter: a sibling
    (same parent: "3.2.2" and "3.2.3" under "3.2"), the parent's own introduction ("3.2"), or a
    section of the same chapter one level up ("3.3" after "3.2.3"). Never the same section, never
    a passage without headings, never another chapter (nothing in common)."""
    own, other = list(own), list(other)
    if not own or not other or own == other:
        return False
    common = 0
    for a, b in zip(own, other, strict=False):
        if a != b:
            break
        common += 1
    return common >= 1 and common >= min(len(own), len(other)) - 1


def reach(depth: int, scope: str) -> int:
    """How far from a kept passage neighbours may be looked for (positions to fetch)."""
    return 2 * depth + 1 if scope == CHAPTER and depth > 0 else depth


def walk_to_next_sections(
    anchor: RetrievedChunk,
    positions: Mapping[Position, RetrievedChunk],
    chosen: dict[Position, RetrievedChunk],
    present: set[Position],
    budget: int,
    depth: int,
    next_pages: Callable[[RetrievedChunk, RetrievedChunk], bool],
) -> int:
    """Carry ``anchor``'s run on, on each side, to the first passage of the nearby section (see
    the module docstring). ``positions``: the fetched passages and the anchors, by (document,
    chunk index). Adds to ``chosen`` (``neighbour_of`` and ``neighbour_scope`` set) and returns
    the budget left."""
    if anchor.chunk_index is None:
        return budget
    own = section_path(anchor)
    for side in (-1, 1):
        edge = anchor.chunk_index
        while (anchor.document_id, edge + side) in chosen or (anchor.document_id, edge + side) in present:
            edge += side
        previous = positions.get((anchor.document_id, edge), anchor)
        if section_path(previous) != own:
            continue  # the run already left the section (another kept passage's)
        walk: list[RetrievedChunk] = []
        reached = False
        for step in range(1, depth + 2):
            position = (anchor.document_id, edge + step * side)
            chunk = positions.get(position)
            if chunk is None or position in chosen or position in present or not next_pages(previous, chunk):
                break
            path = section_path(chunk)
            if path != own and not near_sections(own, path):
                break
            walk.append(chunk)
            previous = chunk
            if path != own:
                reached = True
                break
        cost = sum(len(c.content) for c in walk)
        if not reached or cost > budget:
            continue
        budget -= cost
        for chunk in walk:
            chunk.neighbour_of = anchor.id
            chunk.neighbour_scope = SECTION if section_path(chunk) == own else CHAPTER
            chosen[(chunk.document_id, chunk.chunk_index)] = chunk
    return budget

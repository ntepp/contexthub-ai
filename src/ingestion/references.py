"""Product references (model numbers, part numbers) found in documents and questions.

A reference is a code made of letters and digits — ``US30010CNG``, ``RT-2000``,
``BP10001.5TSA-RM`` — at least two letters and three digits, starting with a letter.
Manuals often factor a range of models: ``US30010/15/20/30/40CNG`` stands for
US30010CNG, US30015CNG, US30020CNG, US30030CNG and US30040CNG (each variant replaces
the last digits of the first code, the shared suffix comes last). Nothing here knows a
manufacturer: the rules are about the shape of the codes only.

Two references match when their keys (upper case, separators other than the decimal
point removed) are equal, or when one is the other without its letter suffix
("US30010" names the US30010CNG, but "US10001" does not name the "US10001.5TSARM").
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence

# Candidate tokens: a letter, then letters/digits, with inner ".", "-" or "/" separators.
_TOKEN = re.compile(r"(?<![\w.\-/])[A-Za-z][A-Za-z0-9]*(?:[.\-/][A-Za-z0-9]+)*")
_SUFFIX = r"(?P<suffix>[A-Za-z][A-Za-z0-9\-]*)?"
_FIRST = re.compile(r"(?P<head>[A-Za-z][A-Za-z0-9\-]*?)(?P<int>\d+)(?P<dec>\.\d+)?" + _SUFFIX)
_VARIANT = re.compile(r"(?P<int>\d+)(?P<dec>\.\d+)?" + _SUFFIX)
_KEY_DROP = re.compile(r"[^A-Z0-9.]")

MIN_LETTERS = 2
MIN_DIGITS = 3
MAX_LENGTH = 40


def reference_key(reference: str) -> str:
    return _KEY_DROP.sub("", reference.upper())


def _is_reference(token: str, case_sensitive: bool) -> bool:
    if not (4 <= len(token) <= MAX_LENGTH) or not token[0].isalpha() or token.count(".") > 1:
        return False
    letters = [ch for ch in token if ch.isalpha()]
    digits = sum(ch.isdigit() for ch in token)
    if len(letters) < MIN_LETTERS or digits < MIN_DIGITS:
        return False
    return not case_sensitive or all(ch.isupper() for ch in letters)


def _expand(token: str, case_sensitive: bool) -> list[str] | None:
    """``US30010/15/20/30/40CNG`` → the five codes; ``None`` when ``token`` is not a factored range."""
    parts = token.split("/")
    first = _FIRST.fullmatch(parts[0])
    variants = [_VARIANT.fullmatch(part) for part in parts[1:]]
    if first is None or not all(variants):
        return None
    suffix = first.group("suffix") or variants[-1].group("suffix") or ""
    if first.group("suffix") and any(v.group("suffix") for v in variants):
        return None  # "ABC12X/34Y": not a factored range
    if any(v.group("suffix") for v in variants[:-1]):
        return None  # only the last variant carries the shared suffix
    base = first.group("int")
    codes = [first.group("head") + base + (first.group("dec") or "") + suffix]
    for variant in variants:
        digits = variant.group("int")
        if len(digits) > len(base):
            return None
        codes.append(first.group("head") + base[: len(base) - len(digits)] + digits + (variant.group("dec") or "") + suffix)
    codes = [code for code in codes if _is_reference(code, case_sensitive)]
    return codes or None


def extract_references(text: str, case_sensitive: bool = True) -> list[str]:
    """References in ``text``, in order of appearance, without duplicates (by key).

    ``case_sensitive``: documents write codes in capitals; a question may not ("us30010cng").
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(code: str) -> None:
        display = code.upper() if not case_sensitive else code
        key = reference_key(display)
        if key not in seen:
            seen.add(key)
            found.append(display)

    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).rstrip(".-")
        if "/" in token:
            expanded = _expand(token, case_sensitive)
            if expanded is not None:
                for code in expanded:
                    add(code)
                continue
            for part in token.split("/"):
                if _is_reference(part, case_sensitive):
                    add(part)
        elif _is_reference(token, case_sensitive):
            add(token)
    return found


def question_references(question: str) -> list[str]:
    return extract_references(question, case_sensitive=False)


def keys_match(asked: str, known: str) -> bool:
    if asked == known:
        return True
    shorter, longer = sorted((asked, known), key=len)
    return longer.startswith(shorter) and longer[len(shorter)].isalpha()


def matching(asked: Iterable[str], known: Iterable[str]) -> list[str]:
    """The ``known`` references that one of the ``asked`` ones names."""
    asked_keys = [reference_key(a) for a in asked]
    return [k for k in known if any(keys_match(a, reference_key(k)) for a in asked_keys)]


def document_references(prominent: Sequence[str], pages: Sequence[str], limit: int = 64) -> tuple[list[str], list[str]]:
    """(every reference of the document, the prominent ones).

    ``prominent`` texts (title, first page, running headers and footers) say what the
    document is about: their references come first and head every indexed passage. The
    others follow by number of pages that cite them; all of them count when a question's
    reference is matched against the document.
    """
    heading: list[str] = []
    seen: set[str] = set()
    for text in prominent:
        for ref in extract_references(text):
            if reference_key(ref) not in seen:
                seen.add(reference_key(ref))
                heading.append(ref)
    counts: Counter[str] = Counter()
    first_seen: dict[str, tuple[int, str]] = {}
    for page_number, text in enumerate(pages):
        for ref in extract_references(text):
            key = reference_key(ref)
            counts[key] += 1
            first_seen.setdefault(key, (page_number, ref))
    others = sorted((k for k in counts if k not in seen), key=lambda k: (-counts[k], first_seen[k][0]))
    return (heading + [first_seen[k][1] for k in others])[:limit], heading[:8]

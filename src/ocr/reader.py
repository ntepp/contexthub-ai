"""One page image in, text and confidence out: preprocessing, orientation, recognition."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from PIL import Image

from src.ocr import preprocess
from src.ocr.base import SURE_ORIENTATION, OcrEngine, OcrResult


@dataclass
class PageReading:
    text: str
    confidence: float | None
    blank: bool = False
    rotation: int = 0  # clockwise degrees applied (orientation detection)
    skew: float = 0.0  # counter-clockwise degrees applied (deskew)
    upscaled: float = 1.0


def useful_chars(text: str) -> int:
    """Letters and digits: whitespace, punctuation and rules do not make a text layer."""
    return sum(ch.isalnum() for ch in text)


_LETTER_RUN = re.compile(r"[^\W\d_]+")
_DIGIT = re.compile(r"\d")
_CID = re.compile(r"\(cid:\d+\)")  # unmapped glyph as some extractors write it
_VOWELS = frozenset("aeiouy")
# Private use, unassigned, surrogate and control code points: glyphs without a Unicode meaning.
_GARBAGE_CATEGORIES = frozenset({"Co", "Cn", "Cs", "Cc"})


def _plausible_word(word: str) -> bool:
    """A run of letters that may be a word: in Latin script it holds a vowel ("J", "BCB" or a
    "DRV" label does not), single letters only when they are a vowel; other scripts pass."""
    bases = [unicodedata.normalize("NFKD", ch)[0].lower() for ch in word]
    latin = [b for b in bases if b.isascii()]
    if not latin:
        return True
    if len(word) == 1:
        return latin[0] in _VOWELS
    return any(b in _VOWELS for b in latin)


def _quality_counts(text: str) -> tuple[int, int, int]:
    """(letters in plausible words, letters + garbage glyphs, digits)."""
    garbage = 5 * len(_CID.findall(text))
    text = _CID.sub(" ", text)
    garbage += sum(
        1 for ch in text if ch == "\ufffd" or (unicodedata.category(ch) in _GARBAGE_CATEGORIES and not ch.isspace())
    )
    letters = good = 0
    for word in _LETTER_RUN.findall(text):
        letters += len(word)
        if _plausible_word(word):
            good += len(word)
    return good, letters + garbage, len(_DIGIT.findall(text))


def text_quality(text: str) -> float | None:
    """Share (0-1) of the page's letters and glyphs that belong to plausible words.

    Low for a text layer that cannot be read: glyphs of fonts without a Unicode table
    (private use area ``\\uf0b2``, ``(cid:12)``, replacement characters), stray labels of a
    drawing whose real text is an image (``J5``, ``BCB_DRV``, ``L4``). Digits are neutral
    (a table of figures is not garbage). ``None`` when the text holds no letter at all.
    """
    good, total, _ = _quality_counts(text)
    return round(good / total, 3) if total else None


def readable_chars(text: str, digits: bool = True) -> int:
    """Letters of plausible words, and digits unless ``digits=False``: how much of ``text``
    can actually be read (the digits of an unreadable layer — drawing labels — are not text)."""
    good, _, figures = _quality_counts(text)
    return good + (figures if digits else 0)


def _better(a: OcrResult, b: OcrResult) -> bool:
    """Whether reading ``b`` beats ``a`` (confidence first, then amount of text)."""
    return (b.confidence or 0.0, b.words) > (a.confidence or 0.0, a.words)


def read_page(
    engine: OcrEngine,
    image: Image.Image,
    *,
    languages: str,
    timeout: float,
    target_dpi: int,
    min_confidence: float = 60.0,
    prepare: bool = True,
) -> PageReading:
    """Read one page. ``prepare=False`` skips orientation, upscaling and deskew (used by the
    evaluation to measure what preprocessing brings). Raises ``OcrError`` / ``OcrTimeout``.

    Orientation: a confident detection is applied as is. An unsure one (or a poor reading
    of the page as it stands) is checked by reading the page both ways (the detected
    rotation, or 180° for an upside-down scan) and keeping the better reading."""
    gray = preprocess.to_grayscale(image)
    if preprocess.is_blank(gray):
        return PageReading(text="", confidence=None, blank=True)
    if not prepare:
        result = engine.recognize(gray, languages, timeout)
        return PageReading(text=result.text, confidence=result.confidence)
    gray, factor = preprocess.upscale(gray, target_dpi)

    def attempt(rotation: int) -> tuple[OcrResult, float]:
        straight, skew = preprocess.deskew(preprocess.rotate_clockwise(gray, rotation))
        return engine.recognize(straight, languages, timeout), skew

    rotation, sure = engine.detect_orientation(gray, timeout)
    result, skew = attempt(rotation)
    if sure < SURE_ORIENTATION and (rotation or (result.confidence or 0.0) < min_confidence):
        candidate = 0 if rotation else 180
        other, other_skew = attempt(candidate)
        if _better(result, other):
            rotation, result, skew = candidate, other, other_skew
    return PageReading(
        text=result.text, confidence=result.confidence, rotation=rotation, skew=skew, upscaled=factor
    )

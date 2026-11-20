"""One page image in, text and confidence out: preprocessing, orientation, recognition."""

from __future__ import annotations

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

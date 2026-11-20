"""The OCR port: what ingestion needs from an engine, nothing more."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image

# Orientation confidence (Tesseract OSD scale) above which no second reading is tried.
SURE_ORIENTATION = 1.0


class OcrError(Exception):
    """The engine could not read one page (crash, missing binary, bad image)."""


class OcrTimeout(OcrError):
    """The engine exceeded OCR_PAGE_TIMEOUT_SECONDS on one page."""


@dataclass
class OcrResult:
    text: str
    # Mean word confidence, 0-100; None when the engine recognised no word.
    confidence: float | None
    words: int


@dataclass
class EngineInfo:
    available: bool
    version: str | None = None
    languages: list[str] = field(default_factory=list)
    error: str | None = None


class OcrEngine(Protocol):
    name: str

    def probe(self) -> EngineInfo:
        """Whether the engine can run here, its version and installed languages (never raises)."""
        ...

    def detect_orientation(self, image: Image.Image, timeout: float) -> tuple[int, float]:
        """Clockwise rotation (0, 90, 180 or 270) that makes the page upright, with the
        engine's confidence in it (``SURE_ORIENTATION`` and above: apply without checking);
        ``(0, 0.0)`` when the engine cannot tell."""
        ...

    def recognize(self, image: Image.Image, languages: str, timeout: float) -> OcrResult:
        """Text of the page with its confidence. Raises OcrError / OcrTimeout."""
        ...

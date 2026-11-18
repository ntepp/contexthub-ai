"""Picks the OCR engine; tests replace it with a fake (same pattern as ``src/usage/registry``)."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from src.config import get_settings
from src.ocr.base import EngineInfo, OcrEngine
from src.ocr.tesseract import TesseractEngine

_override: Callable[[], OcrEngine | None] | None = None


def set_ocr_engine_override(factory: Callable[[], OcrEngine | None] | None) -> None:
    """For tests: replace the engine by a fake (``None`` restores Tesseract)."""
    global _override
    _override = factory
    _default_info.cache_clear()


def _build() -> OcrEngine | None:
    return _override() if _override is not None else TesseractEngine()


@lru_cache
def _default_info() -> EngineInfo:
    """Probed once per process: installing Tesseract later needs a worker restart."""
    engine = _build()
    return engine.probe() if engine is not None else EngineInfo(available=False, error="no_engine")


def get_ocr_engine() -> OcrEngine | None:
    """The engine to read scanned pages with, or None when OCR is disabled, unavailable or
    has none of the configured languages."""
    if not get_settings().ocr_enabled:
        return None
    engine = _build()
    if engine is None or not _default_info().available or not ocr_languages():
        return None
    return engine


def ocr_languages() -> str:
    """Configured languages that are installed, Tesseract style (``fra+eng``).

    A missing language would make every page fail: it is left out (and reported by the
    operations check) rather than blocking the others."""
    installed = set(_default_info().languages)
    return "+".join(lang for lang in configured_languages() if lang in installed)


def ocr_ready() -> bool:
    return get_ocr_engine() is not None


def configured_languages() -> list[str]:
    return [lang.strip() for lang in get_settings().ocr_languages.split("+") if lang.strip()]


def engine_check() -> dict:
    """Operations check for ``/admin/system`` (probed live, not cached)."""
    settings = get_settings()
    engine = _build()
    info = engine.probe() if engine is not None else EngineInfo(available=False, error="no_engine")
    notes: list[str] = []
    check: dict = {
        "ok": info.available or not settings.ocr_enabled,
        "enabled": settings.ocr_enabled,
        "engine": engine.name if engine is not None else None,
        "available": info.available,
        "version": info.version,
        "languages": info.languages,
        "configured_languages": configured_languages(),
    }
    if not settings.ocr_enabled:
        notes.append("OCR désactivé (OCR_ENABLED=false) : les pages numérisées ne sont pas lues.")
    elif not info.available:
        check["error"] = info.error
        notes.append(
            "Tesseract introuvable : les documents texte s'indexent normalement, "
            "les images et pages numérisées sont signalées comme non lues."
        )
    else:
        missing = [lang for lang in configured_languages() if lang not in info.languages]
        if missing and len(missing) == len(configured_languages()):
            check["ok"] = False
            check["error"] = "no_language"
            notes.append(
                f"Aucune des langues OCR configurées n'est installée ({', '.join(missing)}) : "
                "les pages numérisées ne sont pas lues."
            )
        elif missing:
            notes.append(
                f"Langues OCR configurées mais non installées : {', '.join(missing)} "
                "(paquets tesseract-ocr-<langue>)."
            )
        if "osd" not in info.languages:
            notes.append("Données « osd » absentes : l'orientation des pages tournées n'est pas corrigée.")
    check["notes"] = notes
    return check

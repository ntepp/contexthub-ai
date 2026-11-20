"""Default OCR adapter: the local Tesseract binary through pytesseract.

Nothing leaves the server. Every pytesseract failure is mapped to ``OcrError`` so that
the worker never mistakes it for a transient error (``TesseractNotFoundError`` is an
``OSError``, which the indexing task would otherwise retry).
"""

from __future__ import annotations

from PIL import Image

from src.ocr.base import EngineInfo, OcrError, OcrResult, OcrTimeout


class TesseractEngine:
    name = "tesseract"

    def probe(self) -> EngineInfo:
        try:
            import pytesseract

            version = str(pytesseract.get_tesseract_version()).splitlines()[0]
            languages = sorted(lang for lang in pytesseract.get_languages(config="") if lang)
            return EngineInfo(available=True, version=version, languages=languages)
        except Exception as exc:
            return EngineInfo(available=False, error=type(exc).__name__)

    def detect_orientation(self, image: Image.Image, timeout: float) -> tuple[int, float]:
        import pytesseract

        try:
            osd = pytesseract.image_to_osd(
                image,
                config="--psm 0 -c min_characters_to_try=5",
                output_type=pytesseract.Output.DICT,
                timeout=timeout,
            )
        except Exception:  # too few characters, osd data missing, timeout: keep the page as is
            return 0, 0.0
        rotate = int(osd.get("rotate", 0)) % 360
        if rotate not in (0, 90, 180, 270):
            return 0, 0.0
        return rotate, float(osd.get("orientation_conf", 0.0))

    def recognize(self, image: Image.Image, languages: str, timeout: float) -> OcrResult:
        import pytesseract

        try:
            data = pytesseract.image_to_data(
                image, lang=languages, output_type=pytesseract.Output.DICT, timeout=timeout
            )
        except RuntimeError as exc:
            if "timeout" in str(exc).lower():
                raise OcrTimeout(str(exc)) from exc
            raise OcrError(str(exc)) from exc
        except Exception as exc:
            raise OcrError(f"{type(exc).__name__}: {exc}") from exc
        return result_from_data(data)


def result_from_data(data: dict[str, list]) -> OcrResult:
    """Rebuild the page text (lines, blank line between paragraphs) and its mean word confidence."""
    lines: list[str] = []
    confidences: list[float] = []
    current_line: tuple[int, int, int] | None = None
    current_par: tuple[int, int] | None = None
    words: list[str] = []
    for i, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        if key != current_line:
            if words:
                lines.append(" ".join(words))
                words = []
            if current_par is not None and key[:2] != current_par:
                lines.append("")
            current_line, current_par = key, key[:2]
        words.append(word)
        confidences.append(conf)
    if words:
        lines.append(" ".join(words))
    confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
    return OcrResult(text="\n".join(lines), confidence=confidence, words=len(confidences))

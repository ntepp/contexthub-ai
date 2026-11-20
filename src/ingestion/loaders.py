"""Format-specific text extraction.

Every loader returns ``PageText`` items. Headings found in structured formats (DOCX
styles, HTML ``<h1>``–``<h6>``) are emitted as Markdown ``#`` lines so the chunker
can attach a section label to each chunk regardless of the source format.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from src.config import get_settings
from src.ingestion.filetypes import IMAGE_TYPES, SUPPORTED_TYPES, IngestionError, decode_text
from src.ocr.base import OcrEngine, OcrError
from src.ocr.reader import read_page, useful_chars
from src.ocr.registry import get_ocr_engine, ocr_languages
from src.utils.logging_config import get_logger

log = get_logger()

# Below this many letters and digits a page has no text layer at all (as opposed to a poor
# one, see OCR_MIN_PAGE_CHARS): only such pages are read by OCR when they hold no image.
_NO_TEXT_CHARS = 20
_BLANK_CHECK_DPI = 50

READ_NATIVE = "native"
READ_OCR = "ocr"
READ_PARTIAL = "partial"

# "run": read scanned pages now; "defer": raise OcrRequired so that the document is
# indexed on the dedicated "ocr" queue (see src/workers/tasks.py).
OcrMode = Literal["run", "defer"]
ProgressCallback = Callable[[int, int], None]


class OcrRequired(Exception):
    """The document has pages to read by OCR: index it on the "ocr" queue."""


@dataclass
class OcrOptions:
    mode: OcrMode = "run"
    all_pages: bool = False  # PDF: OCR every page, not only the poor ones (manual re-run)
    on_progress: ProgressCallback | None = None


@dataclass
class PageText:
    number: int | None  # 1-based page number, None for unpaginated formats
    text: str


@dataclass
class LoadedDocument:
    pages: list[PageText]
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    ocr_pages: int = 0  # pages whose text comes from OCR
    low_pages: list[int] = field(default_factory=list)  # badly read or unread pages (1-based)
    ocr_confidence: float | None = None  # mean of the OCR pages' confidence (0-100)
    ocr_attempted: bool = False

    @property
    def read_mode(self) -> str:
        if self.low_pages:
            return READ_PARTIAL
        return READ_OCR if self.ocr_pages else READ_NATIVE


def _pages_label(numbers: Sequence[int]) -> str:
    return ", ".join(str(n) for n in numbers)


def _read_pages(
    loaded: LoadedDocument,
    indices: Sequence[int],
    render: Callable[[int], Image.Image],
    engine: OcrEngine,
    options: OcrOptions,
) -> None:
    """OCR ``indices`` of ``loaded.pages`` in place; keep the native text when it is richer."""
    settings = get_settings()
    languages = ocr_languages()
    confidences: list[float] = []
    uncertain: list[int] = []
    failed: list[int] = []
    loaded.ocr_attempted = True
    for done, index in enumerate(indices, start=1):
        page = loaded.pages[index]
        number = page.number or index + 1
        try:
            reading = read_page(
                engine,
                render(index),
                languages=languages,
                timeout=settings.ocr_page_timeout_seconds,
                target_dpi=settings.ocr_dpi,
                min_confidence=settings.ocr_min_confidence,
            )
        except OcrError as exc:
            log.warning("[ocr] page %d non lue : %s", number, exc)
            failed.append(number)
            reading = None
        if reading is not None and not reading.blank:
            if reading.rotation or reading.skew:
                log.info("[ocr] page %d redressée (%d°, %.1f°)", number, reading.rotation, reading.skew)
            ocr_chars, native_chars = useful_chars(reading.text), useful_chars(page.text)
            if ocr_chars and (options.all_pages or ocr_chars > native_chars):
                page.text = reading.text
                loaded.ocr_pages += 1
                if reading.confidence is not None:
                    confidences.append(reading.confidence)
                if reading.confidence is None or reading.confidence < settings.ocr_min_confidence:
                    uncertain.append(number)
            elif not native_chars:
                uncertain.append(number)  # ink on the page, but nothing recognised
        if options.on_progress is not None:
            options.on_progress(done, len(indices))

    if confidences:
        loaded.ocr_confidence = round(sum(confidences) / len(confidences), 1)
    loaded.low_pages = sorted(set(loaded.low_pages) | set(uncertain) | set(failed))
    if loaded.ocr_pages:
        loaded.warnings.append(f"{loaded.ocr_pages} page(s) numérisée(s) traitée(s) par OCR.")
    if uncertain:
        loaded.warnings.append(
            f"Lecture incertaine (confiance OCR inférieure à {settings.ocr_min_confidence:g} %) : "
            f"page(s) {_pages_label(uncertain)}."
        )
    if failed:
        loaded.warnings.append(f"OCR en échec (erreur ou délai dépassé) : page(s) {_pages_label(failed)}.")


@contextmanager
def _pdf_renderer(path: Path, dpi: int) -> Iterator[Callable[[int], Image.Image]]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        yield lambda index: pdf[index].render(scale=dpi / 72, grayscale=True).to_pil()
    finally:
        pdf.close()


def _has_image(page) -> bool:
    """Whether the page draws an image XObject (a scan), without decoding it."""
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        return any(xobjects[name].get_object().get("/Subtype") == "/Image" for name in xobjects)
    except Exception:
        return True  # unknown: let OCR have a look


def _non_blank(path: Path, indices: Sequence[int]) -> list[int]:
    from src.ocr.preprocess import is_blank

    try:
        with _pdf_renderer(path, _BLANK_CHECK_DPI) as render:
            return [i for i in indices if not is_blank(render(i))]
    except Exception:
        return list(indices)


def load_pdf(path: Path, options: OcrOptions | None = None) -> LoadedDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    options = options or OcrOptions()
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise IngestionError("PDF protégé par mot de passe : retirez la protection puis réimportez-le.")
    except PdfReadError as exc:
        raise IngestionError(f"PDF illisible : {exc}") from exc

    settings = get_settings()
    pages: list[PageText] = []
    poor: list[int] = []
    for index, page in enumerate(reader.pages):
        try:
            content = page.extract_text() or ""
        except Exception as exc:  # pypdf raises a zoo of errors on damaged pages
            log.warning("[ingestion] page %d illisible : %s", index + 1, exc)
            content = ""
        chars = useful_chars(content)
        if chars < settings.ocr_min_page_chars and (chars < _NO_TEXT_CHARS or _has_image(page)):
            poor.append(index)
        pages.append(PageText(index + 1, content))

    loaded = LoadedDocument(pages=pages, page_count=len(pages))
    targets = list(range(len(pages))) if options.all_pages else poor
    if not targets:
        return loaded
    engine = get_ocr_engine()
    if engine is None:
        unread = [i + 1 for i in _non_blank(path, poor)]
        loaded.low_pages = unread
        if unread:
            loaded.warnings.append(
                f"{len(unread)} page(s) sans couche texte ignorée(s) (OCR désactivé ou Tesseract absent) : "
                f"page(s) {_pages_label(unread)}."
            )
        return loaded
    if options.mode == "defer":
        raise OcrRequired()
    with _pdf_renderer(path, settings.ocr_dpi) as render:
        _read_pages(loaded, targets, render, engine, options)
    return loaded


def load_image(path: Path, options: OcrOptions | None = None) -> LoadedDocument:
    """JPEG, PNG or TIFF: one document, one cited page per TIFF frame."""
    options = options or OcrOptions()
    try:
        image = Image.open(path)
        frames = getattr(image, "n_frames", 1)
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        raise IngestionError(f"Image illisible : {exc}") from exc
    try:
        loaded = LoadedDocument(pages=[PageText(i + 1, "") for i in range(frames)], page_count=frames)
        engine = get_ocr_engine()
        if engine is None:
            raise IngestionError(
                "Image non lue : l'OCR est désactivé ou Tesseract est absent du serveur. "
                "Relancez l'OCR une fois le service disponible."
            )
        if options.mode == "defer":
            raise OcrRequired()

        def frame(index: int) -> Image.Image:
            try:
                image.seek(index)
                image.load()
            except (Image.DecompressionBombError, OSError, ValueError, EOFError) as exc:
                raise OcrError(f"image illisible : {exc}") from exc
            return image.copy()

        _read_pages(loaded, range(frames), frame, engine, options)
        return loaded
    finally:
        image.close()


def load_docx(path: Path) -> LoadedDocument:
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise IngestionError(f"Document Word illisible : {exc}") from exc

    lines: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = docx.text.paragraph.Paragraph(block, document)
            content = paragraph.text.strip()
            if not content:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
            if style.startswith(("heading", "titre", "title")):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                lines.append(f"{'#' * min(int(level), 6)} {content}")
            else:
                lines.append(content)
        elif tag == "tbl":
            table = docx.table.Table(block, document)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
    return LoadedDocument(pages=[PageText(None, "\n".join(lines))], page_count=0)


def load_text(path: Path) -> LoadedDocument:
    return LoadedDocument(pages=[PageText(None, decode_text(path.read_bytes()))], page_count=0)


def load_html_bytes(raw: bytes) -> LoadedDocument:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(decode_text(raw), "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        tag.decompose()
    for level in range(1, 7):
        for heading in soup.find_all(f"h{level}"):
            heading.replace_with(f"\n{'#' * level} {heading.get_text(' ', strip=True)}\n")
    content = soup.get_text("\n")
    lines = [line.strip() for line in content.splitlines()]
    return LoadedDocument(pages=[PageText(None, "\n".join(line for line in lines if line))], page_count=0)


def load_file(path: Path, mime_type: str, ocr: OcrOptions | None = None) -> LoadedDocument:
    if mime_type == SUPPORTED_TYPES[".pdf"]:
        return load_pdf(path, ocr)
    if mime_type in IMAGE_TYPES:
        return load_image(path, ocr)
    if mime_type == SUPPORTED_TYPES[".docx"]:
        return load_docx(path)
    if mime_type == "text/html":
        return load_html_bytes(path.read_bytes())
    if mime_type in ("text/plain", "text/markdown"):
        return load_text(path)
    raise IngestionError(f"Type de fichier non supporté : {mime_type}")

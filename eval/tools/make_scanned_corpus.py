"""Build the "documents numérisés" corpus from HR fiches already in the repository.

    python -m eval.tools.make_scanned_corpus [--out eval/corpora/rh_scans]

Reproducible (fixed seeds, DejaVu Sans when installed, Pillow's own font otherwise) and
offline: every file is rendered from ``eval/corpora/rh`` (Service-Public.fr, Etalab 2.0).
Each output exercises one degradation of real scans:

| file | source | degradation |
|---|---|---|
| ``F2278-…-scan.tiff`` | congé pour décès | multipage TIFF, every page turned 90°, 200 dpi |
| ``F2883-…-scan.pdf`` | démission | image-only PDF, upside down, 1.5° skew, specks, blur |
| ``F2405-…-mixte.pdf`` | jours fériés | odd pages with a text layer, even pages scanned |
| ``F13851-…-photo.jpg`` | télétravail (page 1) | 100 dpi, JPEG quality 35, specks |
| ``F1643-…-page1.png`` | période d'essai (page 1) | 150 dpi, 2.5° skew |

``manifest.json`` lists every file with, per page, the text that was rendered (the
reference for ``eval.tools.measure_ocr``) and whether the page is scanned.
The generated files are not versioned (``.gitignore``): run this script first.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SOURCE_DIR = Path("eval/corpora/rh")
DEFAULT_OUT = Path("eval/corpora/rh_scans")
DPI = 300
PAGE = (2480, 3508)  # A4 at 300 dpi
MARGIN = 220
BODY_PX, HEADING_PX = 40, 50  # about 9.6 pt and 12 pt at 300 dpi
_FONT_DIRS = (Path("/usr/share/fonts/truetype/dejavu"), Path("C:/Windows/Fonts"), Path("/Library/Fonts"))


@dataclass
class Spec:
    source: str
    output: str
    kind: str  # tiff | scan_pdf | mixed_pdf | jpeg | png
    first_page_only: bool = False
    rotate: int = 0  # clockwise degrees, as found on the scanner glass
    skew: float = 0.0
    dpi: int = DPI
    specks: float = 0.0  # share of pixels flipped
    blur: float = 0.0
    jpeg_quality: int = 85
    seed: int = 0
    notes: list[str] = field(default_factory=list)


SPECS = [
    Spec("F2278-conge-pour-le-deces-d-un-membre-de-la-famille-dun-salarie-du.md",
         "F2278-conge-deces-scan.tiff", "tiff", rotate=90, dpi=200, seed=1),
    Spec("F2883-demission-d-un-salarie.md", "F2883-demission-scan.pdf", "scan_pdf",
         rotate=180, skew=1.5, specks=0.002, blur=0.8, seed=2),
    Spec("F2405-jours-feries-et-ponts-dans-le-secteur-prive.md", "F2405-jours-feries-mixte.pdf", "mixed_pdf",
         specks=0.001, seed=3),
    Spec("F13851-teletravail-du-salarie-dans-le-secteur-prive.md", "F13851-teletravail-photo.jpg", "jpeg",
         first_page_only=True, dpi=100, specks=0.001, jpeg_quality=35, seed=4),
    Spec("F1643-periode-d-essai-pour-un-salarie.md", "F1643-periode-essai-page1.png", "png",
         first_page_only=True, dpi=150, skew=2.5, seed=5),
]


# --------------------------------------------------------------------------- text


def markdown_lines(markdown: str) -> list[tuple[str, bool]]:
    """(text, is_heading) per paragraph; Markdown syntax removed."""
    out: list[tuple[str, bool]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = line.startswith("#")
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("**", "").replace("__", "")
        line = re.sub(r"^[-*]\s+", "• ", line)
        out.append((line, heading))
    return out


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for directory in _FONT_DIRS:
        if (directory / name).is_file():
            return ImageFont.truetype(str(directory / name), size)
    return ImageFont.load_default(size=size)


def _wrap(text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def layout(paragraphs: list[tuple[str, bool]]) -> list[list[tuple[str, bool]]]:
    """Paragraphs → pages of (line, is_heading)."""
    body, heading = _font(BODY_PX), _font(HEADING_PX, bold=True)
    width, bottom = PAGE[0] - 2 * MARGIN, PAGE[1] - MARGIN
    pages: list[list[tuple[str, bool]]] = [[]]
    y = MARGIN
    for text, is_heading in paragraphs:
        font = heading if is_heading else body
        step = int((HEADING_PX if is_heading else BODY_PX) * 1.45)
        wrapped = _wrap(text, font, width)
        if is_heading and y + step * 3 > bottom:  # no heading alone at the bottom of a page
            pages.append([])
            y = MARGIN
        for line in wrapped:
            if y + step > bottom:
                pages.append([])
                y = MARGIN
            pages[-1].append((line, is_heading))
            y += step
        y += step // 2
    return [p for p in pages if p]


def render(page: list[tuple[str, bool]]) -> Image.Image:
    image = Image.new("L", PAGE, 255)
    draw = ImageDraw.Draw(image)
    body, heading = _font(BODY_PX), _font(HEADING_PX, bold=True)
    y = MARGIN
    previous_heading = None
    for line, is_heading in page:
        if previous_heading is not None and previous_heading != is_heading:
            y += BODY_PX // 2
        draw.text((MARGIN, y), line, fill=0, font=heading if is_heading else body)
        y += int((HEADING_PX if is_heading else BODY_PX) * 1.45)
        previous_heading = is_heading
    return image


def page_text(page: list[tuple[str, bool]]) -> str:
    return "\n".join(line for line, _ in page)


# --------------------------------------------------------------------------- degradations


def degrade(image: Image.Image, spec: Spec, rng: random.Random) -> Image.Image:
    if spec.skew:
        image = image.rotate(spec.skew, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    if spec.rotate:
        image = image.rotate(-spec.rotate, expand=True, fillcolor=255)
    if spec.dpi != DPI:
        factor = spec.dpi / DPI
        image = image.resize((round(image.width * factor), round(image.height * factor)), Image.Resampling.LANCZOS)
    if spec.blur:
        image = image.filter(ImageFilter.GaussianBlur(spec.blur))
    if spec.specks:
        pixels = image.load()
        for _ in range(int(image.width * image.height * spec.specks)):
            x, y = rng.randrange(image.width), rng.randrange(image.height)
            pixels[x, y] = 0 if rng.random() < 0.5 else 255
    return image


def text_pdf(lines: list[str]) -> bytes:
    """One A4 page with a real text layer (Helvetica, WinAnsi: French accents survive)."""

    def escape(text: str) -> bytes:
        raw = text.encode("cp1252", errors="replace")
        return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    stream = b"BT /F1 9.6 Tf 14 TL 53 789 Td " + b"".join(b"(" + escape(line) + b") Tj T* " for line in lines) + b"ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def image_pdf(image: Image.Image, dpi: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=float(dpi))
    return buffer.getvalue()


def merge(parts: list[bytes]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for part in parts:
        for page in PdfReader(io.BytesIO(part)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- build


def build(spec: Spec, out: Path) -> dict:
    rng = random.Random(spec.seed)
    pages = layout(markdown_lines((SOURCE_DIR / spec.source).read_text(encoding="utf-8")))
    if spec.first_page_only:
        pages = pages[:1]
    entries, images, parts = [], [], []
    for number, page in enumerate(pages, start=1):
        scanned = spec.kind != "mixed_pdf" or number % 2 == 0
        entries.append({"page": number, "scanned": scanned, "text": page_text(page)})
        if not scanned:
            parts.append(text_pdf([line for line, _ in page]))
            continue
        image = degrade(render(page), spec, rng)
        images.append(image)
        if spec.kind in ("scan_pdf", "mixed_pdf"):
            parts.append(image_pdf(image, spec.dpi))
    target = out / spec.output
    if spec.kind == "tiff":
        images[0].save(target, format="TIFF", save_all=True, append_images=images[1:], compression="tiff_lzw",
                       dpi=(spec.dpi, spec.dpi))
    elif spec.kind == "jpeg":
        images[0].save(target, format="JPEG", quality=spec.jpeg_quality, dpi=(spec.dpi, spec.dpi))
    elif spec.kind == "png":
        images[0].save(target, format="PNG", dpi=(spec.dpi, spec.dpi))
    else:
        target.write_bytes(merge(parts))
    return {
        "file": spec.output,
        "source": spec.source,
        "kind": spec.kind,
        "degradation": {"rotate": spec.rotate, "skew": spec.skew, "dpi": spec.dpi, "specks": spec.specks,
                        "blur": spec.blur, "jpeg_quality": spec.jpeg_quality if spec.kind == "jpeg" else None},
        "pages": entries,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    font = _font(BODY_PX)
    manifest = {
        "generator": "eval/tools/make_scanned_corpus.py",
        "font": getattr(font, "path", "Pillow default"),
        "documents": [build(spec, args.out) for spec in SPECS],
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for doc in manifest["documents"]:
        scanned = sum(p["scanned"] for p in doc["pages"])
        print(f"{doc['file']} : {len(doc['pages'])} page(s), dont {scanned} numérisée(s)")


if __name__ == "__main__":
    main()

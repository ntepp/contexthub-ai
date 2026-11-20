"""Measure OCR on the "documents numérisés" corpus, with and without preprocessing.

    python -m eval.tools.make_scanned_corpus
    python -m eval.tools.measure_ocr [--corpus eval/corpora/rh_scans] [--lang fra+eng] [--json out.json]
    python -m eval.tools.measure_ocr --file F2883-demission-scan.pdf --pages 1-3 --append rows.jsonl
    python -m eval.tools.measure_ocr --summarize rows.jsonl   # report of runs made piece by piece

For every page, the share of reference characters found in the recognised text
(``difflib`` matching blocks, case and whitespace folded) and Tesseract's mean
confidence. Scanned pages are read twice through ``src.ocr.reader.read_page``: as the
indexing pipeline does (orientation, upscaling, deskew) and without preprocessing; the
pages of the mixed PDF that carry a text layer are measured on that layer. The text
was rendered in French: measured with ``eng`` only, accents are lost and the rate
understates what ``fra`` gives (see the report printed at the end).
"""

from __future__ import annotations

import argparse
import difflib
import json
import time
import unicodedata
from pathlib import Path

from PIL import Image

from eval.tools.make_scanned_corpus import DEFAULT_OUT
from src.ocr.reader import read_page
from src.ocr.tesseract import TesseractEngine


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def recognised_rate(reference: str, text: str, accents: bool = True) -> float:
    """Share of the reference characters found, in order, in ``text`` (0-1)."""
    ref, got = _fold(reference), _fold(text)
    if not accents:
        ref, got = _strip_accents(ref), _strip_accents(got)
    if not ref:
        return 1.0
    matcher = difflib.SequenceMatcher(None, ref, got, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(ref)


def page_images(path: Path) -> list[Image.Image | None]:
    """One image per page; None for a PDF page that has a text layer (mixed PDF)."""
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        from pypdf import PdfReader

        native = [len((p.extract_text() or "").strip()) > 50 for p in PdfReader(str(path)).pages]
        pdf = pdfium.PdfDocument(str(path))
        try:
            return [None if native[i] else pdf[i].render(scale=300 / 72, grayscale=True).to_pil()
                    for i in range(len(pdf))]
        finally:
            pdf.close()
    image = Image.open(path)
    frames = []
    for index in range(getattr(image, "n_frames", 1)):
        image.seek(index)
        frames.append(image.copy())
    return frames


def native_text(path: Path, index: int) -> str:
    from pypdf import PdfReader

    return PdfReader(str(path)).pages[index].extract_text() or ""


def _page_range(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    first, _, last = spec.partition("-")
    return set(range(int(first), int(last or first) + 1))


def measure(
    corpus: Path,
    languages: str,
    timeout: float = 180,
    only_file: str | None = None,
    pages: set[int] | None = None,
    append: Path | None = None,
) -> dict:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    engine = TesseractEngine()
    info = engine.probe()
    if not info.available:
        raise SystemExit("Tesseract introuvable : mesure impossible.")
    missing = [lang for lang in languages.split("+") if lang not in info.languages]
    if missing:
        raise SystemExit(f"Langue(s) Tesseract absente(s) : {', '.join(missing)} (installées : {info.languages}).")
    rows = []
    for doc in manifest["documents"]:
        if only_file and doc["file"] != only_file:
            continue
        path = corpus / doc["file"]
        for entry, image in zip(doc["pages"], page_images(path)):
            if pages is not None and entry["page"] not in pages:
                continue
            row = {"file": doc["file"], "page": entry["page"], "scanned": entry["scanned"]}
            if image is None:
                text = native_text(path, entry["page"] - 1)
                row["native"] = round(recognised_rate(entry["text"], text), 3)
            else:
                for label, prepare in (("prepared", True), ("raw", False)):
                    started = time.time()
                    reading = read_page(engine, image, languages=languages, timeout=timeout, target_dpi=300,
                                        prepare=prepare)
                    row[label] = {
                        "rate": round(recognised_rate(entry["text"], reading.text), 3),
                        "rate_without_accents": round(recognised_rate(entry["text"], reading.text, accents=False), 3),
                        "confidence": reading.confidence,
                        "rotation": reading.rotation,
                        "skew": reading.skew,
                        "seconds": round(time.time() - started, 1),
                    }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if append is not None:
                with append.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({**row, "tesseract": info.version, "languages": languages}) + "\n")
    return {"tesseract": info.version, "languages": languages, "installed": info.languages, "pages": rows}


def summary(result: dict) -> str:
    scanned = [r for r in result["pages"] if "prepared" in r]

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    lines = [f"Tesseract {result['tesseract']}, langues {result['languages']} — {len(scanned)} page(s) numérisée(s)"]
    for label in ("prepared", "raw"):
        lines.append(
            f"  {'avec' if label == 'prepared' else 'sans'} prétraitement : caractères reconnus "
            f"{mean([r[label]['rate'] for r in scanned]):.1%} (hors accents "
            f"{mean([r[label]['rate_without_accents'] for r in scanned]):.1%}), confiance moyenne "
            f"{mean([r[label]['confidence'] or 0 for r in scanned]):.1f}"
        )
    for name in dict.fromkeys(r["file"] for r in scanned):
        pages = [r for r in scanned if r["file"] == name]
        lines.append(
            f"  {name} : avec {mean([r['prepared']['rate_without_accents'] for r in pages]):.1%}, "
            f"sans {mean([r['raw']['rate_without_accents'] for r in pages]):.1%} (hors accents)"
        )
    native = [r["native"] for r in result["pages"] if "native" in r]
    if native:
        lines.append(f"  pages à couche texte (PDF mixte) : {mean(native):.1%}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lang", default="fra+eng")
    parser.add_argument("--json", type=Path, help="écrit le détail par page dans ce fichier")
    parser.add_argument("--file", help="un seul fichier du corpus")
    parser.add_argument("--pages", help="pages de ce fichier, ex. 2 ou 1-3")
    parser.add_argument("--append", type=Path, help="ajoute chaque page mesurée à ce fichier JSONL")
    parser.add_argument("--summarize", type=Path, help="rapport d'un fichier JSONL produit par --append")
    args = parser.parse_args(argv)
    if args.summarize:
        rows = [json.loads(line) for line in args.summarize.read_text(encoding="utf-8").splitlines() if line]
        first = rows[0] if rows else {}
        print(summary({"tesseract": first.get("tesseract"), "languages": first.get("languages"), "pages": rows}))
        return
    result = measure(args.corpus, args.lang, only_file=args.file, pages=_page_range(args.pages), append=args.append)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary(result))


if __name__ == "__main__":
    main()

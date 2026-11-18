"""Supported formats and content sniffing.

The client-supplied extension is only a hint: the first bytes of the file must match
the format, so a renamed executable or archive is rejected before it reaches a parser.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

SUPPORTED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

TEXT_TYPES = {"text/plain", "text/markdown", "text/html"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/tiff"}
# Formats that may need OCR: every image, and PDFs with scanned pages.
OCR_TYPES = IMAGE_TYPES | {"application/pdf"}

# Magic numbers of the accepted images (TIFF: little/big endian, classic and BigTIFF).
_IMAGE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/tiff": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
}
_IMAGE_LABELS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/tiff": "TIFF"}


class IngestionError(Exception):
    """Permanent failure: retrying with the same input cannot succeed."""


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def mime_for(filename: str) -> str | None:
    return SUPPORTED_TYPES.get(extension_of(filename))


def sniff(path: Path, declared_mime: str) -> None:
    """Raise IngestionError if the file content does not match its declared type."""
    with path.open("rb") as fh:
        head = fh.read(8192)
    if declared_mime == "application/pdf":
        if b"%PDF-" not in head[:1024]:
            raise IngestionError("Le fichier n'est pas un PDF valide.")
        return
    if declared_mime == SUPPORTED_TYPES[".docx"]:
        if not head.startswith(b"PK\x03\x04"):
            raise IngestionError("Le fichier n'est pas un document Word (.docx) valide.")
        try:
            with zipfile.ZipFile(path) as zf:
                if "word/document.xml" not in zf.namelist():
                    raise IngestionError("Archive .docx sans contenu Word.")
        except zipfile.BadZipFile as exc:
            raise IngestionError("Document Word corrompu.") from exc
        return
    if declared_mime in IMAGE_TYPES:
        if not head.startswith(_IMAGE_SIGNATURES[declared_mime]):
            raise IngestionError(f"Le fichier n'est pas une image {_IMAGE_LABELS[declared_mime]} valide.")
        return
    if declared_mime in TEXT_TYPES:
        if b"\x00" in head:
            raise IngestionError("Le fichier texte contient des données binaires.")
        try:
            decode_text(head, strict=True)
        except UnicodeDecodeError as exc:
            raise IngestionError("Encodage du fichier texte non reconnu.") from exc
        return
    raise IngestionError("Type de fichier non supporté.")


def decode_text(raw: bytes, strict: bool = False) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if strict:
            # A truncated multi-byte sequence at the end of the sniffed head is fine.
            try:
                return raw[:-4].decode("utf-8-sig")
            except UnicodeDecodeError:
                pass
        return raw.decode("cp1252")

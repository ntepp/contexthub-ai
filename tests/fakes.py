"""Deterministic stand-ins for the embedding model, the LLM and PDF files."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

DIM = 128
_WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbeddings(Embeddings):
    """Bag-of-words hashed into a fixed-size vector: shared words => small cosine distance."""

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        for word in _WORD.findall(text.lower()):
            if len(word) < 3:
                continue
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class ScriptedChatModel(BaseChatModel):
    """Chat model whose reply is computed from the prompt by ``responder``."""

    responder: Callable[[list[BaseMessage]], str]
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _reply(self, messages: list[BaseMessage]) -> str:
        self.calls.append(messages)
        return self.responder(messages)

    def _generate(self, messages, stop=None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._reply(messages)))])

    def _stream(
        self, messages, stop=None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        for token in re.findall(r"\S+\s*", self._reply(messages)):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            if run_manager:
                run_manager.on_llm_new_token(token, chunk=chunk)
            yield chunk


def is_condense_prompt(messages: list[BaseMessage]) -> bool:
    return "Standalone question:" in str(messages[-1].content)


def is_plan_prompt(messages: list[BaseMessage]) -> bool:
    return "You plan the searches" in str(messages[-1].content)


def make_pdf(pages: list[list[str]]) -> bytes:
    """Minimal valid PDF, one text line per entry, Helvetica, ASCII only."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # placeholder, patched below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for lines in pages:
        stream_lines = [b"BT", b"/F1 11 Tf", b"14 TL", b"50 780 Td"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_lines.append(b"(" + safe.encode("latin-1") + b") Tj T*")
        stream_lines.append(b"ET")
        stream = b"\n".join(stream_lines)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_obj, content, font)
        )
        kids.append(page)
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)


# ---------------------------------------------------------------------------
# OCR (ADR-003)
# ---------------------------------------------------------------------------


class FakeOcrEngine:
    """Scripted OCR engine: returns ``pages`` in order (text, confidence) or raises the
    exception given in place of a page; records every call."""

    name = "fake"

    def __init__(self, pages=None, languages=("eng", "fra", "osd"), available=True, rotation=0, sure=10.0):
        from src.ocr.base import EngineInfo

        self.pages = list(pages or [])
        self.info = EngineInfo(available=available, version="fake 1.0", languages=list(languages))
        self.rotation = rotation
        self.sure = sure  # orientation confidence: below 1.0 the reader checks a second way
        self.calls: list[dict] = []

    def probe(self):
        return self.info

    def detect_orientation(self, image, timeout):
        return self.rotation, self.sure

    def recognize(self, image, languages, timeout):
        from src.ocr.base import OcrResult

        self.calls.append({"size": image.size, "languages": languages, "timeout": timeout})
        item = self.pages.pop(0) if self.pages else ("Texte reconnu par le faux moteur OCR " * 3, 91.0)
        if isinstance(item, BaseException):
            raise item
        text, confidence = item
        return OcrResult(text=text, confidence=confidence, words=len(text.split()))


def scan_image(size=(1240, 1754), lines=12, angle=0.0):
    """A synthetic scanned page: black text-like bars on white (not blank), grayscale."""
    from PIL import Image, ImageDraw

    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    width, height = size
    step = max(12, height // (lines + 4))
    for i in range(lines):
        y = step * (i + 2)
        draw.rectangle([width // 10, y, width - width // 10 - (i % 3) * width // 12, y + step // 3], fill=0)
    return image.rotate(angle, expand=True, fillcolor=255) if angle else image


def image_bytes(images, fmt: str) -> bytes:
    """Encode one image (or a list: multipage TIFF / PDF) as ``fmt``."""
    import io

    images = images if isinstance(images, list) else [images]
    buffer = io.BytesIO()
    first, rest = images[0], images[1:]
    extra = {"save_all": True, "append_images": rest} if rest else {}
    if fmt == "PDF":
        extra["resolution"] = 150.0
    first.save(buffer, format=fmt, **extra)
    return buffer.getvalue()


def merge_pdfs(*parts: bytes) -> bytes:
    """Concatenate PDFs page by page (mixed native + scanned documents)."""
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for part in parts:
        for page in PdfReader(io.BytesIO(part)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeTorch:
    """The few ``torch`` functions the re-ranker uses (installed in ``sys.modules``)."""

    def __init__(self, cuda: bool = False) -> None:
        self._cuda = cuda
        self.threads: int | None = None
        self.in_inference = False
        self.cuda = self  # torch.cuda.is_available()

    def is_available(self) -> bool:
        return self._cuda

    def set_num_threads(self, count: int) -> None:
        self.threads = count

    def get_num_threads(self) -> int:
        return self.threads or 4

    def inference_mode(self):
        torch = self

        class _Mode:
            def __enter__(self):
                torch.in_inference = True

            def __exit__(self, *exc):
                torch.in_inference = False

        return _Mode()


class FakeCrossEncoder:
    """Stand-in for ``sentence_transformers.CrossEncoder``: scores a (query, passage) pair by
    the query words found in the passage, and records every pair it is asked to score."""

    instances: list[FakeCrossEncoder] = []
    torch: FakeTorch | None = None

    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model, self.kwargs = model, kwargs
        self.pairs: list[tuple[str, str]] = []
        self.calls: list[dict] = []
        FakeCrossEncoder.instances.append(self)

    def predict(self, pairs, batch_size: int = 32, show_progress_bar: bool | None = None, **kwargs: Any):
        torch = FakeCrossEncoder.torch
        self.calls.append(
            {"pairs": len(pairs), "batch_size": batch_size, "inference": bool(torch and torch.in_inference)}
        )
        self.pairs.extend(pairs)
        scores = []
        for query, passage in pairs:
            words = {w for w in _WORD.findall(query.lower()) if len(w) > 2}
            text = passage.lower()
            scores.append(float(sum(1 for w in words if w in text)) + 1.0 / (1 + len(passage)))
        return scores

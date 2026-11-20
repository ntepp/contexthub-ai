"""Scanned documents (ADR-003): images, mixed PDFs, quality, dedicated queue, re-run, operations."""

from __future__ import annotations

import io
import shutil
import uuid

import pytest
from PIL import Image

from src.config import get_settings
from src.ocr import preprocess
from src.ocr import registry as ocr_registry
from src.ocr.base import OcrError, OcrTimeout
from src.ocr.reader import read_page, useful_chars
from src.ocr.tesseract import TesseractEngine, result_from_data
from src.workers import tasks
from src.workers.celery_app import OCR_QUEUE, celery_app
from tests.conftest import grant, upload
from tests.fakes import FakeOcrEngine, image_bytes, make_pdf, merge_pdfs, scan_image

PNG, JPEG, TIFF = "image/png", "image/jpeg", "image/tiff"
NATIVE_PAGE = ["Article 1", "Objet", "La presente politique encadre le teletravail des salaries de l entreprise."]
SCANNED_TEXT = "Article 2\nModalites\nLe salarie en teletravail beneficie des memes droits que les autres salaries."


@pytest.fixture
def ocr(monkeypatch) -> FakeOcrEngine:
    engine = FakeOcrEngine()
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    ocr_registry.set_ocr_engine_override(lambda: engine)
    return engine


@pytest.fixture
def routed(monkeypatch) -> list[str]:
    """Names of the indexing tasks that ran, in order."""
    seen: list[str] = []
    original = tasks._index

    def spy(task, document_id, options):
        seen.append(task.name.rsplit(".", 1)[-1])
        return original(task, document_id, options)

    monkeypatch.setattr(tasks, "_index", spy)
    return seen


def _doc(client, doc_id: str) -> dict:
    return client.get(f"/documents/{doc_id}").json()


def _mixed_pdf() -> bytes:
    """Page 1 has a text layer, page 2 is a scan (image only)."""
    return merge_pdfs(make_pdf([NATIVE_PAGE]), image_bytes(scan_image(), "PDF"))


# ---------------------------------------------------------------------------
# Import and type sniffing
# ---------------------------------------------------------------------------


def test_image_types_are_accepted_and_sniffed(admin, collection, ocr):
    for name, fmt, mime in [("scan.png", "PNG", PNG), ("scan.jpg", "JPEG", JPEG), ("scan.tiff", "TIFF", TIFF)]:
        ocr.pages = [(SCANNED_TEXT + f" ({name})", 90.0)]
        resp = upload(admin, collection["id"], name, image_bytes(scan_image(), fmt), mime)
        assert resp.status_code == 202, (name, resp.text)
        assert _doc(admin, resp.json()["id"])["status"] == "ready", name


def test_image_whose_content_does_not_match_its_extension_is_rejected(admin, collection, ocr):
    png = image_bytes(scan_image(), "PNG")
    resp = upload(admin, collection["id"], "photo.jpg", png, JPEG)
    assert resp.status_code == 400 and "JPEG" in resp.json()["detail"]
    assert upload(admin, collection["id"], "x.png", b"%PDF-1.4 not an image", PNG).status_code == 400
    assert upload(admin, collection["id"], "x.tif", png, TIFF).status_code == 400


def test_image_upload_is_read_by_ocr_and_cited_on_page_one(admin, collection, store, ocr, routed):
    ocr.pages = [(SCANNED_TEXT, 88.5)]
    doc_id = upload(admin, collection["id"], "note.png", image_bytes(scan_image(), "PNG"), PNG).json()["id"]
    doc = _doc(admin, doc_id)
    assert doc["status"] == "ready", doc
    assert routed == ["ocr_document"]  # images go straight to the OCR queue
    assert doc["page_count"] == 1
    assert (doc["read_mode"], doc["ocr_pages"], doc["ocr_confidence"], doc["ocr_low_pages"]) == ("ocr", 1, 88.5, [])
    assert doc["ocr_eligible"] is True
    assert any("traitée(s) par OCR" in w for w in doc["warnings"])
    chunks = [c for c in store.chunks.values() if str(c.document_id) == doc_id]
    assert chunks and {c.metadata["page"] for c in chunks} == {1}
    assert "teletravail" in chunks[0].content
    assert ocr.calls[0]["timeout"] == get_settings().ocr_page_timeout_seconds


def test_multipage_tiff_cites_each_frame_and_serves_page_previews(admin, collection, store, ocr):
    # Long pages: chunks follow sections, a short page would share its chunk with the next one.
    ocr.pages = [(f"Page numero {n} du registre du personnel de l entreprise. " * 40, 90.0) for n in (1, 2, 3)]
    frames = [scan_image(), scan_image(lines=8), scan_image(lines=5)]
    doc_id = upload(admin, collection["id"], "registre.tif", image_bytes(frames, "TIFF"), TIFF).json()["id"]
    doc = _doc(admin, doc_id)
    assert (doc["status"], doc["page_count"], doc["ocr_pages"]) == ("ready", 3, 3)
    pages = {c.metadata["page"]: c.content for c in store.chunks.values()}
    assert set(pages) == {1, 2, 3} and "numero 2" in pages[2]

    preview = admin.get(f"/documents/{doc_id}/pages/2")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(preview.content)).size[0] <= 2000
    assert admin.get(f"/documents/{doc_id}/pages/4").status_code == 404
    assert admin.get(f"/documents/{doc_id}/pages/0").status_code == 404
    pdf_id = upload(admin, collection["id"], "a.pdf", make_pdf([NATIVE_PAGE])).json()["id"]
    assert admin.get(f"/documents/{pdf_id}/pages/1").status_code == 409


def test_page_preview_requires_access(admin, member, collection, ocr):
    doc_id = upload(admin, collection["id"], "n.png", image_bytes(scan_image(), "PNG"), PNG).json()["id"]
    assert member.get(f"/documents/{doc_id}/pages/1").status_code == 404


# ---------------------------------------------------------------------------
# Mixed PDFs, quality and failures
# ---------------------------------------------------------------------------


def test_mixed_pdf_keeps_native_text_and_ocrs_the_scanned_page(admin, collection, store, ocr, routed):
    ocr.pages = [(SCANNED_TEXT, 93.0)]
    doc_id = upload(admin, collection["id"], "politique.pdf", _mixed_pdf()).json()["id"]
    doc = _doc(admin, doc_id)
    assert doc["status"] == "ready", doc
    assert routed == ["ingest_document", "ocr_document"]  # handed over to the OCR queue
    assert len(ocr.calls) == 1  # the native page is not OCRed
    assert (doc["page_count"], doc["ocr_pages"], doc["read_mode"]) == (2, 1, "ocr")
    pages = {c.metadata["page"]: c.content for c in store.chunks.values()}
    assert "encadre le teletravail" in pages[1] and "memes droits" in pages[2]
    # The OCR page was rendered at OCR_DPI (A4 at 300 dpi is about 2480 x 3508).
    assert max(ocr.calls[0]["size"]) > 3000


def _scan_with_text_layer(lines: list[str]) -> bytes:
    """A scanned page that also carries a short text layer (header or page number)."""
    from pypdf import PdfReader, PdfWriter

    scan = PdfReader(io.BytesIO(image_bytes(scan_image(), "PDF"))).pages[0]
    scan.merge_page(PdfReader(io.BytesIO(make_pdf([lines]))).pages[0])
    writer = PdfWriter()
    writer.add_page(scan)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_poor_text_layer_is_replaced_only_when_ocr_reads_more(admin, collection, store, ocr):
    pdf = merge_pdfs(make_pdf([NATIVE_PAGE]), _scan_with_text_layer(["p. 2"]), _scan_with_text_layer(["Voir verso"]))
    ocr.pages = [(SCANNED_TEXT, 90.0), ("An", 90.0)]  # page 3: OCR reads less, native kept
    doc_id = upload(admin, collection["id"], "annexes.pdf", pdf).json()["id"]
    doc = _doc(admin, doc_id)
    assert len(ocr.calls) == 2 and doc["ocr_pages"] == 1
    assert doc["read_mode"] == "ocr" and doc["ocr_low_pages"] == []
    contents = " ".join(c.content for c in store.chunks.values())
    assert "memes droits" in contents and "Voir verso" in contents


def test_low_confidence_and_failed_pages_are_reported(admin, collection, ocr):
    scans = image_bytes([scan_image(), scan_image(lines=6), scan_image(lines=9)], "PDF")
    ocr.pages = [(SCANNED_TEXT, 95.0), ("Lecture tres approximative du contenu de la page", 41.0), OcrTimeout("timeout")]
    doc = _doc(admin, upload(admin, collection["id"], "scan.pdf", scans).json()["id"])
    assert doc["status"] == "ready", doc
    assert doc["read_mode"] == "partial"
    assert doc["ocr_low_pages"] == [2, 3]
    assert doc["ocr_pages"] == 2 and doc["ocr_confidence"] == 68.0
    assert any("Lecture incertaine" in w and "page(s) 2" in w for w in doc["warnings"])
    assert any("OCR en échec" in w and "page(s) 3" in w for w in doc["warnings"])


def test_blank_pages_are_neither_ocred_nor_reported(admin, collection, ocr):
    blank = Image.new("L", (1240, 1754), 255)
    pdf = merge_pdfs(make_pdf([NATIVE_PAGE]), image_bytes(blank, "PDF"))
    doc = _doc(admin, upload(admin, collection["id"], "blanc.pdf", pdf).json()["id"])
    assert ocr.calls == []
    assert (doc["read_mode"], doc["ocr_low_pages"], doc["ocr_pages"]) == ("native", [], 0)


def test_image_without_any_recognised_text_fails_with_explanation(admin, collection, ocr):
    ocr.pages = [("", None)]
    doc = _doc(admin, upload(admin, collection["id"], "vide.png", image_bytes(scan_image(), "PNG"), PNG).json()["id"])
    assert doc["status"] == "failed"
    assert "Aucun texte reconnu" in doc["error_message"]


def test_engine_error_is_not_retried_as_transient(admin, collection, ocr, monkeypatch):
    # TesseractNotFoundError is an OSError: the adapter must turn it into OcrError.
    import pytesseract

    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    with pytest.raises(OcrError):
        TesseractEngine().recognize(scan_image(), "eng", 5)


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


def test_ocr_task_is_routed_to_its_own_queue():
    router = celery_app.amqp.router
    assert router.route({}, "src.workers.tasks.ocr_document")["queue"].name == OCR_QUEUE
    assert router.route({}, "src.workers.tasks.ingest_document")["queue"].name == "celery"


def test_text_document_never_goes_through_the_ocr_queue(admin, collection, ocr, routed):
    upload(admin, collection["id"], "note.txt", b"Note interne sur la procedure de teletravail du groupe.", "text/plain")
    upload(admin, collection["id"], "native.pdf", make_pdf([NATIVE_PAGE]))
    assert routed == ["ingest_document", "ingest_document"]
    assert ocr.calls == []


def test_ocr_worker_consumes_only_the_ocr_queue(monkeypatch):
    from src.workers.ocr_worker import worker_argv

    monkeypatch.setattr(get_settings(), "ocr_worker_concurrency", 2)
    argv = worker_argv(["--pool=solo"])
    assert argv[0] == "worker" and "--queues=ocr" in argv and "--concurrency=2" in argv
    assert argv[-1] == "--pool=solo"


def test_queue_notes_flag_a_missing_ocr_worker(monkeypatch):
    from src.api.routes.admin import queue_notes

    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    assert queue_notes({"w@h": [{"name": "celery"}], "ocr@h": [{"name": "ocr"}]}) == []
    assert any("worker OCR" in n for n in queue_notes({"w@h": [{"name": "celery"}]}))
    assert any("par défaut" in n for n in queue_notes({"ocr@h": [{"name": "ocr"}]}))
    assert queue_notes({}) == []


# ---------------------------------------------------------------------------
# Re-run
# ---------------------------------------------------------------------------


def test_rerun_ocr_is_idempotent_audited_and_can_read_every_page(admin, collection, store, ocr, routed):
    ocr.pages = [(SCANNED_TEXT, 50.0)]
    doc_id = upload(admin, collection["id"], "m.pdf", _mixed_pdf()).json()["id"]
    before = store.count_document(uuid.UUID(doc_id))
    assert _doc(admin, doc_id)["read_mode"] == "partial"

    ocr.pages = [(SCANNED_TEXT, 92.0)]
    resp = admin.post(f"/documents/{doc_id}/ocr")
    assert resp.status_code == 202, resp.text
    assert resp.json()["read_mode"] == "ocr" and resp.json()["status"] == "ready"
    assert store.count_document(uuid.UUID(doc_id)) == before  # chunks replaced, not added
    assert routed[-1] == "ocr_document"

    calls = len(ocr.calls)
    ocr.pages = [(" ".join(NATIVE_PAGE) + " (relu)", 97.0), (SCANNED_TEXT, 92.0)]
    resp = admin.post(f"/documents/{doc_id}/ocr", json={"all_pages": True})
    assert resp.status_code == 202 and len(ocr.calls) == calls + 2
    assert any("(relu)" in c.content for c in store.chunks.values())

    audit = [e for e in admin.get("/admin/audit").json()["items"] if e["action"] == "document.ocr"]
    assert len(audit) == 2 and audit[0]["details"] in ({"all_pages": True}, {"all_pages": False})


def test_rerun_ocr_requires_editor_and_an_ocr_format(admin, member, member_user, collection, ocr):
    pdf_id = upload(admin, collection["id"], "m.pdf", _mixed_pdf()).json()["id"]
    txt_id = upload(admin, collection["id"], "n.txt", b"Une note interne assez longue pour etre indexee.", "text/plain").json()["id"]
    assert member.post(f"/documents/{pdf_id}/ocr").status_code == 404
    grant(admin, collection["id"], member_user.id, "viewer")
    assert member.post(f"/documents/{pdf_id}/ocr").status_code == 403
    assert admin.post(f"/documents/{txt_id}/ocr").status_code == 409
    assert _doc(admin, txt_id)["ocr_eligible"] is False


def test_rerun_ocr_without_engine_answers_503(admin, collection, monkeypatch):
    doc_id = upload(admin, collection["id"], "m.pdf", _mixed_pdf()).json()["id"]
    assert admin.post(f"/documents/{doc_id}/ocr").status_code == 503


# ---------------------------------------------------------------------------
# Without Tesseract
# ---------------------------------------------------------------------------


@pytest.fixture
def no_tesseract(monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    engine = FakeOcrEngine(available=False)
    ocr_registry.set_ocr_engine_override(lambda: engine)
    return engine


def test_without_tesseract_text_is_indexed_and_scanned_pages_flagged(admin, collection, store, no_tesseract, routed):
    txt = upload(admin, collection["id"], "n.txt", b"Une note interne assez longue pour etre indexee.", "text/plain")
    assert _doc(admin, txt.json()["id"])["status"] == "ready"

    doc = _doc(admin, upload(admin, collection["id"], "m.pdf", _mixed_pdf()).json()["id"])
    assert doc["status"] == "ready"
    assert (doc["read_mode"], doc["ocr_low_pages"], doc["ocr_pages"]) == ("partial", [2], 0)
    assert any("sans couche texte ignorée(s)" in w for w in doc["warnings"])

    img = _doc(admin, upload(admin, collection["id"], "s.png", image_bytes(scan_image(), "PNG"), PNG).json()["id"])
    assert img["status"] == "failed" and "Tesseract est absent" in img["error_message"]
    assert "ocr_document" not in routed  # nothing is parked on a queue nobody can serve
    assert no_tesseract.calls == []


def test_admin_system_reports_the_ocr_engine(admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    monkeypatch.setattr(get_settings(), "ocr_languages", "fra+eng")
    engine = FakeOcrEngine(languages=("eng", "osd"))
    ocr_registry.set_ocr_engine_override(lambda: engine)
    status = admin.get("/admin/system").json()
    check = status["checks"]["ocr"]
    assert check["ok"] and check["available"] and check["version"] == "fake 1.0"
    assert check["languages"] == ["eng", "osd"] and check["configured_languages"] == ["fra", "eng"]
    assert any("fra" in n for n in check["notes"])
    assert status["configuration"]["ocr"]["available"] is True
    assert ocr_registry.ocr_languages() == "eng"  # the missing language is left out

    engine.info.available = False
    ocr_registry.set_ocr_engine_override(lambda: engine)
    check = admin.get("/admin/system").json()["checks"]["ocr"]
    assert not check["ok"] and any("Tesseract introuvable" in n for n in check["notes"])

    monkeypatch.setattr(get_settings(), "ocr_enabled", False)
    check = admin.get("/admin/system").json()["checks"]["ocr"]
    assert check["ok"] and not check["enabled"] and any("désactivé" in n for n in check["notes"])


def test_no_installed_configured_language_disables_ocr(monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    monkeypatch.setattr(get_settings(), "ocr_languages", "deu")
    ocr_registry.set_ocr_engine_override(lambda: FakeOcrEngine(languages=("eng",)))
    assert ocr_registry.get_ocr_engine() is None
    check = ocr_registry.engine_check()
    assert not check["ok"] and check["error"] == "no_language"


# ---------------------------------------------------------------------------
# Preprocessing and parsing
# ---------------------------------------------------------------------------


def test_low_resolution_scans_are_upscaled_towards_the_target_dpi():
    small = scan_image(size=(620, 877))  # A4 at ~75 dpi
    scaled, factor = preprocess.upscale(small, 300)
    assert factor == 3.0 and scaled.size == (1860, 2631)
    full = scan_image(size=(2480, 3508))
    assert preprocess.upscale(full, 300) == (full, 1.0)


def test_blank_detection():
    assert preprocess.is_blank(Image.new("L", (1000, 1400), 255))
    assert not preprocess.is_blank(scan_image())


def test_skew_is_detected_and_corrected():
    assert preprocess.skew_angle(scan_image(lines=20)) == 0.0
    tilted = scan_image(lines=20, angle=3.0)
    assert abs(preprocess.skew_angle(tilted) + 3.0) <= 0.5
    straightened, angle = preprocess.deskew(tilted)
    assert angle and preprocess.skew_angle(straightened) == 0.0


def test_orientation_reported_by_the_engine_is_applied():
    engine = FakeOcrEngine(rotation=90)
    page = scan_image(size=(1240, 1754)).rotate(90, expand=True, fillcolor=255)  # landscape
    reading = read_page(engine, page, languages="eng", timeout=5, target_dpi=150)
    assert reading.rotation == 90
    width, height = engine.calls[0]["size"]
    assert height > width  # upright portrait page sent to recognition


def test_unsure_orientation_is_checked_by_a_second_reading():
    engine = FakeOcrEngine(pages=[("bad", 20.0), ("Lecture correcte de la page retournee", 91.0)], sure=0.0)
    reading = read_page(engine, scan_image(), languages="eng", timeout=5, target_dpi=150)
    assert (reading.rotation, reading.confidence, len(engine.calls)) == (180, 91.0, 2)

    engine = FakeOcrEngine(pages=[("Lecture correcte de la page", 90.0)], sure=0.0)
    assert read_page(engine, scan_image(), languages="eng", timeout=5, target_dpi=150).rotation == 0
    assert len(engine.calls) == 1  # good reading, no detected rotation: no second pass


def test_tesseract_data_is_rebuilt_into_lines_and_paragraphs():
    data = {
        "text": ["", "Article", "2", "Le", "salarie", "", "Fin"],
        "conf": [-1, 90, 80, 70, 60, -1, 50],
        "block_num": [1, 1, 1, 1, 1, 2, 2],
        "par_num": [1, 1, 1, 1, 1, 1, 1],
        "line_num": [1, 1, 1, 2, 2, 1, 1],
    }
    result = result_from_data(data)
    assert result.text == "Article 2\nLe salarie\n\nFin"
    assert result.confidence == 70.0 and result.words == 5
    assert result_from_data({"text": [], "conf": []}).confidence is None


def test_useful_chars_ignore_punctuation_and_rules():
    assert useful_chars("— . . . | 12 ___") == 2


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_adds_and_removes_the_quality_columns():
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("m4257", "alembic/versions/69ab4257_document_ocr_quality.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "69ab4256"

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE hub_documents (id VARCHAR(36) PRIMARY KEY, filename VARCHAR(500))"))
        conn.execute(sa.text("INSERT INTO hub_documents VALUES ('a', 'scan.pdf')"))
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            migration.upgrade()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("hub_documents")}
            assert {"read_mode", "ocr_pages", "ocr_confidence", "ocr_low_pages"} <= columns
            assert conn.execute(sa.text("SELECT ocr_pages FROM hub_documents")).scalar_one() == 0
            migration.downgrade()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("hub_documents")}
            assert columns == {"id", "filename"}


# ---------------------------------------------------------------------------
# Real Tesseract (skipped when the binary is absent)
# ---------------------------------------------------------------------------

_HAS_TESSERACT = shutil.which("tesseract") is not None and TesseractEngine().probe().available


def _english_page(angle: int = 0) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    image = Image.new("L", (2480, 3508), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    lines = [
        "Remote work policy of the company.",
        "Employees may work from home two days per week.",
        "The manager approves each request in writing.",
        "Equipment is provided and insured by the employer.",
    ] * 3
    for i, line in enumerate(lines):
        draw.text((220, 300 + i * 110), line, fill=0, font=font)
    return image.rotate(angle, expand=True, fillcolor=255) if angle else image


@pytest.mark.skipif(not _HAS_TESSERACT, reason="Tesseract absent")
@pytest.mark.parametrize("angle", [90, 180])
def test_real_tesseract_reads_a_rotated_scan(angle):
    engine = TesseractEngine()
    if "osd" not in engine.probe().languages:
        pytest.skip("données osd absentes")
    reading = read_page(engine, _english_page(angle), languages="eng", timeout=120, target_dpi=300)
    assert reading.rotation in (90, 180, 270)
    text = reading.text.lower()
    assert "employees may work from home" in text and "insured by the employer" in text
    assert reading.confidence is not None and reading.confidence > 80


@pytest.mark.skipif(not _HAS_TESSERACT, reason="Tesseract absent")
def test_real_tesseract_end_to_end_upload(admin, collection, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    ocr_registry.set_ocr_engine_override(None)
    ocr_registry._default_info.cache_clear()
    small = _english_page(180).resize((1240, 1754))  # upside-down, 150 dpi
    doc_id = upload(admin, collection["id"], "policy.png", image_bytes(small, "PNG"), PNG).json()["id"]
    doc = _doc(admin, doc_id)
    assert doc["status"] == "ready", doc
    assert doc["read_mode"] == "ocr" and doc["ocr_confidence"] > 80
    assert any("work from home" in c.content.lower() for c in store.chunks.values())

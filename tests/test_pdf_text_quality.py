"""PDF pages whose extracted text cannot be read are read by OCR (OCR_MIN_TEXT_QUALITY)."""

from __future__ import annotations

import pytest

from src.config import get_settings
from src.ocr import registry as ocr_registry
from src.ocr.reader import readable_chars, text_quality
from tests.conftest import upload
from tests.fakes import FakeOcrEngine, make_pdf

PROSE = [
    "Chapitre 3 Ecran LCD",
    "Le panneau de controle comporte quatre touches et un ecran tactile.",
    "La page d accueil affiche la fenetre d informations systeme et la fenetre de menu.",
    "Les icones de l ecran sont decrites dans le tableau suivant, avec leur fonction.",
]
# What a drawing whose text is an image leaves in the text layer: wiring labels and numbers,
# well above OCR_MIN_PAGE_CHARS letters and digits.
LABELS = ["J5 J6 J7 L4 L4 L4 L1", " ".join(str(n) for n in range(1, 41)), "BCB_DRV BCB_CNT +24V +24V"]
OCR_TEXT = "Selectionnez l'icone pour desactiver le buzzer declenche par les alarmes generales."


@pytest.mark.parametrize(
    "text, low",
    [
        ("Le panneau de contrôle comporte quatre touches et un écran.", False),
        ("Привет, это обычный текст.", False),
        ("Tableau 3.3 : 230 V, 50 Hz, 10 kVA, 40 batteries", False),
        ("    I", True),
        ("(cid:12)(cid:13)(cid:14)(cid:15) le", True),
        ("���� ab", True),
        ("1 2 3 4 L4 L4 L1 J10 40 39 38", True),
        ("BCB_DRV BCB_CNT J5 J6 +24V", True),
    ],
)
def test_text_quality_separates_text_from_glyphs_and_labels(text, low):
    quality = text_quality(text)
    assert quality is not None
    assert (quality < 0.5) is low, quality


def test_text_quality_ignores_digits_and_counts_readable_characters():
    assert text_quality("12 345 6789 — 3.2.2") is None  # no letter: no verdict, digits are not garbage
    assert text_quality("") is None
    assert readable_chars("Bonjour 12  BCB") == 9
    assert readable_chars("Bonjour 12  BCB", digits=False) == 7


@pytest.fixture
def ocr(monkeypatch) -> FakeOcrEngine:
    engine = FakeOcrEngine()
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    ocr_registry.set_ocr_engine_override(lambda: engine)
    return engine


def _contents(store) -> str:
    return " ".join(c.content for c in store.chunks.values())


def test_unreadable_text_layer_goes_to_ocr_even_above_the_character_threshold(admin, collection, store, ocr):
    assert sum(ch.isalnum() for ch in " ".join(LABELS)) > get_settings().ocr_min_page_chars
    ocr.pages = [(OCR_TEXT, 88.0)]
    pdf = make_pdf([PROSE, LABELS])
    doc_id = upload(admin, collection["id"], "manuel.pdf", pdf).json()["id"]
    doc = admin.get(f"/documents/{doc_id}").json()
    assert doc["status"] == "ready", doc
    assert len(ocr.calls) == 1  # the prose page keeps its text layer
    assert (doc["read_mode"], doc["ocr_pages"]) == ("ocr", 1)
    contents = _contents(store)
    # The OCR reading wins although the labels hold more raw letters and digits than it.
    assert "desactiver le buzzer" in contents and "BCB_DRV" not in contents
    assert "quatre touches" in contents


def test_unreadable_page_keeps_its_labels_when_ocr_reads_nothing_better(admin, collection, store, ocr):
    ocr.pages = [("", None)]
    doc_id = upload(admin, collection["id"], "schema.pdf", make_pdf([PROSE, LABELS + ["Generator output"]])).json()["id"]
    assert admin.get(f"/documents/{doc_id}").json()["ocr_pages"] == 0
    assert "Generator output" in _contents(store)


def test_threshold_is_configurable(admin, collection, store, ocr, monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_min_text_quality", 0.0)
    doc_id = upload(admin, collection["id"], "schema.pdf", make_pdf([PROSE, LABELS])).json()["id"]
    assert ocr.calls == []
    doc = admin.get(f"/documents/{doc_id}").json()
    assert doc["read_mode"] == "native", doc

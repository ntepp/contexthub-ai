"""The "documents numérisés" set (ADR-003): dataset, generator, measurement helper."""

import json

from eval.harness.loader import load_golden_dataset, validate_dataset
from eval.tools import make_scanned_corpus as corpus
from eval.tools.measure_ocr import recognised_rate
from src.config import DEFAULT_NO_ANSWER
from src.ingestion.filetypes import mime_for, sniff

DATASET = "eval/datasets/golden_rh_scans.json"


def test_scanned_dataset_is_valid_and_matches_the_generator():
    ds = load_golden_dataset(DATASET)
    assert validate_dataset(ds, DEFAULT_NO_ANSWER) == []
    assert len(ds.questions) == 20
    assert sum(not q.answerable for q in ds.questions) == 4
    outputs = {spec.output for spec in corpus.SPECS}
    assert set(ds.metadata["expected_documents"]) == outputs
    assert {d for q in ds.questions for d in q.expected_documents} <= outputs
    assert ds.metadata["corpus_dir"] == corpus.DEFAULT_OUT.as_posix()


def test_generator_writes_every_format_with_its_reference_text(tmp_path):
    corpus.main(["--out", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    kinds = {d["kind"] for d in manifest["documents"]}
    assert kinds == {"tiff", "scan_pdf", "mixed_pdf", "jpeg", "png"}
    for doc in manifest["documents"]:
        path = tmp_path / doc["file"]
        sniff(path, mime_for(doc["file"]))  # accepted by the upload checks
        assert doc["pages"] and all(p["text"] for p in doc["pages"])
    mixed = next(d for d in manifest["documents"] if d["kind"] == "mixed_pdf")
    assert [p["scanned"] for p in mixed["pages"][:2]] == [False, True]

    from pypdf import PdfReader

    native = PdfReader(str(tmp_path / mixed["file"])).pages[0].extract_text()
    assert recognised_rate(mixed["pages"][0]["text"], native) > 0.95  # accents survive the text layer


def test_recognised_rate():
    assert recognised_rate("Période d'essai", "periode d'essai", accents=False) == 1.0
    assert recognised_rate("Période d'essai", "periode d'essai") < 1.0
    assert recognised_rate("abc", "") == 0.0

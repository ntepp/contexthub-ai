"""Retrieval-only measurement (eval.tools.measure_retrieval) and the technical manuals set."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from eval.harness.loader import load_golden_dataset, validate_dataset
from eval.tools import measure_retrieval as tool
from src.config import get_settings
from src.retrieval.store import RetrievedChunk
from tests.fakes import make_pdf

DATASET = Path("eval/datasets/golden_manuals.json")


def test_the_manuals_set_is_valid_and_names_pages_and_products():
    ds = load_golden_dataset(DATASET)
    assert validate_dataset(ds, get_settings().no_answer_text) == []
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = raw["questions"]
    assert len(questions) == 20
    assert sum(q["category"] == "trap_other_model" for q in questions) >= 3
    assert sum(not q["answerable"] for q in questions) >= 5
    files = raw["metadata"]["corpus_files"]
    for q in questions:
        assert isinstance(q["product_documents"], list)
        if q["answerable"]:
            assert q["expected_pages"] and q["source_document"] in files
    assert questions[0]["question"].startswith("j'ai une alarme sur mon onduleur Nitram US30010CNG")
    assert questions[0]["expected_pages"] == [42, 43, 44]


def _chunk(filename: str, page: int, page_end: int | None = None, neighbour: bool = False) -> RetrievedChunk:
    meta = {"filename": filename, "page": page, **({"page_end": page_end} if page_end else {})}
    return RetrievedChunk(
        id=uuid.uuid4(), document_id=uuid.uuid4(), collection_id=uuid.uuid4(), content="x" * 10, metadata=meta,
        distance=0.2, neighbour_of=uuid.uuid4() if neighbour else None,
    )


def test_scoring_of_one_question():
    no_answer = get_settings().no_answer_text
    question = {"id": "Q", "category": "c", "answerable": True, "source_document": "a.pdf",
                "expected_pages": [5], "product_documents": ["a.pdf"]}
    anchors = [_chunk("b.pdf", 5), _chunk("a.pdf", 4, 5), _chunk("a.pdf", 5)]
    state = {"anchors": anchors, "context": [*anchors, _chunk("a.pdf", 6, neighbour=True)], "answer": None}
    row = tool.score(question, state, no_answer)
    assert (row["doc_hit"], row["page_hit"], row["page_hit_span"]) == (True, True, True)
    assert row["first_rank"] == 3 and row["reciprocal_rank"] == 0.3333
    assert row["other_product_share"] == 0.25 and row["outcome"] == "llm" and row["passages"] == 4

    trap = {"id": "T", "category": "trap", "answerable": False, "source_document": None,
            "expected_pages": [], "product_documents": [], "expected_notice_refs": ["ZZ123"]}
    notice = tool.score(trap, {"context": [], "answer": f"{no_answer} Les sources trouvées concernent X, pas ZZ123."}, no_answer)
    assert notice["outcome"] == "notice" and notice["notice_ok"] is True
    assert tool.score(trap, {"context": [], "answer": no_answer}, no_answer)["outcome"] == "refused"
    summary = tool.aggregate([row, notice])
    assert summary["page_hit"] == 1.0 and summary["notices_expected_ok"] == 1.0


def test_corpus_files_are_checked_by_name_and_fingerprint(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 a")
    good = hashlib.sha256(b"%PDF-1.4 a").hexdigest()
    assert tool.check_corpus(tmp_path, {"a.pdf": good}) == []
    problems = tool.check_corpus(tmp_path, {"a.pdf": "0" * 64, "b.pdf": good})
    assert len(problems) == 2 and "autre édition" in problems[0] and "absent" in problems[1]


def test_measure_on_an_in_memory_corpus(tmp_path, capsys):
    footer = "ZX2000/10KT Manuel"
    files = {
        "zx.pdf": make_pdf([[title, text, footer] for title, text in [
            ("Ecran", "Pour couper l alarme du buzzer, selectionnez l icone silence."),
            ("Batteries", "Remplacez les batteries tous les trois ans."),
            ("Maintenance", "Nettoyez les grilles de ventilation chaque trimestre."),
        ]]),
        "qw.pdf": make_pdf([["QW5000TS", "Appuyez sur un bouton pour mettre l alarme de l onduleur en silence."]]),
    }
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, content in files.items():
        (corpus / name).write_bytes(content)
    dataset = {
        "metadata": {"collection": "mesure", "corpus_files": {n: hashlib.sha256(c).hexdigest() for n, c in files.items()}},
        "questions": [
            {"id": "A1", "category": "c", "question": "Mon ZX2010KT bipe, comment couper l alarme ?", "ground_truth": "x",
             "source_document": "zx.pdf", "reference_article": None, "answerable": True, "expected_pages": [1, 2, 3],
             "product_documents": ["zx.pdf"]},
            {"id": "T1", "category": "trap_other_model", "question": "Mon AB9990XY bipe, comment couper l alarme ?",
             "ground_truth": get_settings().no_answer_text, "source_document": None, "reference_article": None,
             "answerable": False, "expected_pages": [], "product_documents": [], "expected_notice_refs": ["AB9990XY"]},
        ],
    }
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    assert tool.main(["--dataset", str(path), "--corpus", str(corpus), "--output", str(tmp_path / "out"),
                      "--explain", "T1"]) == 0
    out = capsys.readouterr().out
    assert "Garde-fou produit" in out
    report = json.loads(next((tmp_path / "out").glob("retrieval-*.json")).read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in report["rows"]}
    assert rows["A1"]["doc_hit"] and rows["A1"]["other_product_share"] == 0.0
    assert rows["T1"]["outcome"] == "notice" and rows["T1"]["notice_ok"]

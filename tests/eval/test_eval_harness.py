import csv
import json

from eval.harness.loader import load_golden_dataset, validate_dataset
from eval.harness.metrics import (
    aggregate_results,
    article_hit,
    refusal_exact_match,
    retrieval_hit,
    score_question,
)
from eval.harness.reporter import CSV_COLUMNS, write_report
from src.config import DEFAULT_NO_ANSWER
from tests.fakes import HashEmbeddings

DATASET = "eval/datasets/golden_mifid.json"


def test_golden_dataset_is_valid():
    ds = load_golden_dataset(DATASET)
    assert len(ds.questions) == 18
    assert ds.metadata["version"] == "0.2"
    assert validate_dataset(ds, DEFAULT_NO_ANSWER) == []


def test_validation_catches_inconsistent_traps():
    ds = load_golden_dataset(DATASET)
    trap = next(q for q in ds.questions if not q.answerable)
    trap.ground_truth = "autre chose"
    assert any(trap.id in e for e in validate_dataset(ds, DEFAULT_NO_ANSWER))


def test_refusal_exact_match():
    assert refusal_exact_match(DEFAULT_NO_ANSWER + " ", DEFAULT_NO_ANSWER)
    assert not refusal_exact_match("Je ne sais pas.", DEFAULT_NO_ANSWER)


def test_retrieval_hit_by_filename_and_legacy_path():
    assert retrieval_hit([{"filename": "RAG-CELEX_32014L0065_FR_TXT.pdf"}], "RAG-CELEX_32014L0065_FR_TXT.pdf")
    assert retrieval_hit([{"source": "uploads/abc_RAG-CELEX_32014L0065_FR_TXT.pdf"}], "RAG-CELEX_32014L0065_FR_TXT.pdf")


def test_retrieval_hit_cross_document_requires_all():
    sources = [{"filename": "RAG-CELEX_32012R0648_FR_TXT.pdf"}]
    assert not retrieval_hit(sources, "RAG-CELEX_32012R0648_FR_TXT.pdf; RAG-CELEX_32014R0600_FR_TXT.pdf")


def test_article_hit_prefers_cited_sources():
    sources = [
        {"section": "Article 4 — Définitions", "cited": False},
        {"section": "Article 93 — Transposition", "cited": True},
    ]
    assert article_hit(sources, "Article 93") is True
    assert article_hit(sources, "Article 4, paragraphe 1, point 23)") is False
    assert article_hit(sources, None) is None


def _responses():
    ds = load_golden_dataset(DATASET)
    emb = HashEmbeddings()
    results = []
    for q in ds.questions:
        if q.answerable:
            response = {
                "answer": q.ground_truth,
                "sources": [{"filename": d, "section": q.reference_article, "cited": True} for d in q.expected_documents],
                "confidence_metric": 0.8,
                "latency_seconds": 1.5,
            }
        else:
            response = {"answer": DEFAULT_NO_ANSWER, "sources": [], "confidence_metric": 0.0, "latency_seconds": 0.2}
        results.append(score_question(q, response, no_answer=DEFAULT_NO_ANSWER, embeddings=emb, threshold=0.75))
    return results


def test_perfect_responses_score_perfectly():
    summary = aggregate_results(_responses())
    g = summary["global"]
    assert g["count"] == 18 and g["pass_rate"] == 1.0
    assert g["refusal_accuracy"] == 1.0
    assert g["retrieval_hit_rate"] == 1.0
    assert g["over_refusal_rate"] == 0.0
    assert set(summary["by_category"]) == {
        "factual_simple", "definition", "cross_document", "reasoning_conditional", "unanswerable_trap"
    }


def test_over_refusal_is_counted():
    ds = load_golden_dataset(DATASET)
    q = next(q for q in ds.questions if q.answerable)
    result = score_question(q, {"answer": DEFAULT_NO_ANSWER, "sources": []}, no_answer=DEFAULT_NO_ANSWER,
                            embeddings=HashEmbeddings(), threshold=0.75)
    assert result.over_refusal is True and result.semantic_pass is False


def test_report_files(tmp_path):
    results = _responses()
    summary = {"results": aggregate_results(results)}
    out = write_report(results, summary, tmp_path / "run")
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["results"]["global"]["count"] == 18
    with (out / "details.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 18

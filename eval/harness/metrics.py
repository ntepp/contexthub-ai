"""Scoring of one pipeline response against a golden question, and aggregation.

Metrics
- refusal_match: exact refusal on unanswerable traps (the grounding guarantee)
- over_refusal: refusal on an answerable question (lost recall)
- semantic_score: cosine similarity of bge-m3 embeddings (answer vs ground truth)
- retrieval_hit: every expected document appears among the returned sources
- article_hit: the expected article appears in a *cited* source's section
- calibration: mean retrieval confidence of passing vs failing answers — if they are
  not separated, the confidence shown in the UI carries no information
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean

from eval.harness.loader import GoldenQuestion


def refusal_exact_match(predicted: str, no_answer: str) -> bool:
    return predicted.strip() == no_answer.strip()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _source_name(source: dict) -> str:
    return Path(str(source.get("filename") or source.get("source") or "")).name


def retrieval_hit(sources: list[dict], expected: str | None) -> bool:
    if not expected:
        return True
    expected_docs = [d.strip() for d in expected.split(";") if d.strip()]
    returned = [_source_name(s) for s in sources]
    return all(any(ret == exp or ret.endswith(exp) for ret in returned) for exp in expected_docs)


_ARTICLE_RE = re.compile(r"article\s+(\d+|premier|1er)", re.IGNORECASE)


def _article_numbers(text: str | None) -> set[str]:
    numbers = set()
    for match in _ARTICLE_RE.findall(text or ""):
        numbers.add("1" if match.lower() in ("premier", "1er") else match)
    return numbers


def article_hit(sources: list[dict], reference_article: str | None) -> bool | None:
    expected = _article_numbers(reference_article)
    if not expected:
        return None
    cited = [s for s in sources if s.get("cited")] or sources
    found = set()
    for source in cited:
        found |= _article_numbers(source.get("section"))
        for part in source.get("section_path") or []:
            found |= _article_numbers(part)
    return bool(expected & found)


@dataclass
class QuestionResult:
    id: str
    category: str
    answerable: bool
    question: str
    predicted_answer: str
    ground_truth: str
    refusal_match: bool | None
    over_refusal: bool | None
    semantic_score: float | None
    semantic_pass: bool | None
    retrieval_hit: bool | None
    article_hit: bool | None
    confidence_metric: float
    latency_seconds: float
    sources: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return bool(self.refusal_match) if not self.answerable else bool(self.semantic_pass)


def score_question(q: GoldenQuestion, response: dict, *, no_answer: str, embeddings, threshold: float) -> QuestionResult:
    predicted = response.get("answer", "") or ""
    sources = response.get("sources", []) or []
    refused = refusal_exact_match(predicted, no_answer)
    if q.answerable:
        if refused:
            semantic = 0.0
        else:
            semantic = round(cosine_similarity(embeddings.embed_query(predicted), embeddings.embed_query(q.ground_truth)), 4)
        return QuestionResult(
            id=q.id,
            category=q.category,
            answerable=True,
            question=q.question,
            predicted_answer=predicted,
            ground_truth=q.ground_truth,
            refusal_match=None,
            over_refusal=refused,
            semantic_score=semantic,
            semantic_pass=semantic >= threshold,
            retrieval_hit=retrieval_hit(sources, q.source_document),
            article_hit=article_hit(sources, q.reference_article),
            confidence_metric=float(response.get("confidence_metric", 0.0) or 0.0),
            latency_seconds=float(response.get("latency_seconds", 0.0) or 0.0),
            sources=sources,
        )
    return QuestionResult(
        id=q.id,
        category=q.category,
        answerable=False,
        question=q.question,
        predicted_answer=predicted,
        ground_truth=q.ground_truth,
        refusal_match=refused,
        over_refusal=None,
        semantic_score=None,
        semantic_pass=None,
        retrieval_hit=None,
        article_hit=None,
        confidence_metric=float(response.get("confidence_metric", 0.0) or 0.0),
        latency_seconds=float(response.get("latency_seconds", 0.0) or 0.0),
        sources=sources,
    )


def errored(q: GoldenQuestion, message: str) -> QuestionResult:
    return QuestionResult(
        id=q.id,
        category=q.category,
        answerable=q.answerable,
        question=q.question,
        predicted_answer="",
        ground_truth=q.ground_truth,
        refusal_match=False if not q.answerable else None,
        over_refusal=None,
        semantic_score=0.0 if q.answerable else None,
        semantic_pass=False if q.answerable else None,
        retrieval_hit=False if q.answerable else None,
        article_hit=None,
        confidence_metric=0.0,
        latency_seconds=0.0,
        error=message,
    )


def _rate(values: list[bool | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(sum(kept) / len(kept), 4) if kept else None


def _mean(values: list[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return round(mean(kept), 4) if kept else None


def _block(results: list[QuestionResult]) -> dict:
    answerable = [r for r in results if r.answerable]
    traps = [r for r in results if not r.answerable]
    block = {
        "count": len(results),
        "pass_rate": _rate([r.passed for r in results]),
        "latency_mean_seconds": _mean([r.latency_seconds for r in results if not r.error]),
        "errors": sum(1 for r in results if r.error),
    }
    if traps:
        block["refusal_accuracy"] = _rate([r.refusal_match for r in traps])
    if answerable:
        block.update(
            {
                "semantic_mean": _mean([r.semantic_score for r in answerable]),
                "semantic_pass_rate": _rate([r.semantic_pass for r in answerable]),
                "retrieval_hit_rate": _rate([r.retrieval_hit for r in answerable]),
                "article_hit_rate": _rate([r.article_hit for r in answerable]),
                "over_refusal_rate": _rate([r.over_refusal for r in answerable]),
            }
        )
    return block


def aggregate_results(results: list[QuestionResult]) -> dict:
    by_category: dict[str, list[QuestionResult]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)

    answered = [r for r in results if r.answerable and not r.over_refusal and not r.error]
    passing = [r.confidence_metric for r in answered if r.semantic_pass]
    failing = [r.confidence_metric for r in answered if not r.semantic_pass]
    calibration = {
        "confidence_mean_when_correct": _mean(passing),
        "confidence_mean_when_wrong": _mean(failing),
    }
    if passing and failing:
        calibration["separation"] = round(mean(passing) - mean(failing), 4)

    return {
        "global": _block(results),
        "by_category": {name: _block(items) for name, items in sorted(by_category.items())},
        "calibration": calibration,
    }

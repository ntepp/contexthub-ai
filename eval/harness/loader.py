from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FIELDS = ("id", "category", "question", "ground_truth", "answerable")


@dataclass
class GoldenQuestion:
    id: str
    category: str
    question: str
    ground_truth: str
    source_document: str | None
    reference_article: str | None
    answerable: bool

    @property
    def expected_documents(self) -> list[str]:
        if not self.source_document:
            return []
        return [d.strip() for d in self.source_document.split(";") if d.strip()]


@dataclass
class GoldenDataset:
    metadata: dict
    questions: list[GoldenQuestion]


def load_golden_dataset(path: str | Path) -> GoldenDataset:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = []
    for item in raw.get("questions", []):
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise ValueError(f"Question {item.get('id', '?')} : champs manquants {missing}")
        questions.append(
            GoldenQuestion(
                id=item["id"],
                category=item["category"],
                question=item["question"],
                ground_truth=item["ground_truth"],
                source_document=item.get("source_document"),
                reference_article=item.get("reference_article"),
                answerable=bool(item["answerable"]),
            )
        )
    return GoldenDataset(metadata=raw.get("metadata", {}), questions=questions)


def validate_dataset(ds: GoldenDataset, no_answer: str) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for q in ds.questions:
        if q.id in seen:
            errors.append(f"{q.id} : identifiant en double")
        seen.add(q.id)
        if q.answerable and not q.source_document:
            errors.append(f"{q.id} : question répondable sans source_document")
        if not q.answerable:
            if q.source_document:
                errors.append(f"{q.id} : piège avec un source_document")
            if q.ground_truth.strip() != no_answer.strip():
                errors.append(f"{q.id} : ground_truth du piège différent de NO_ANSWER_TEXT")
    return errors

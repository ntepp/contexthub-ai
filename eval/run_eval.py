"""Run the golden dataset against the live pipeline and write a timestamped report.

    python -m eval.run_eval --dataset eval/datasets/golden_mifid.json --collection mifid
    python -m eval.run_eval --collection mifid --ingest ./corpus/mifid   # index first

Prerequisites: database migrated, embedding model and LLM reachable, collection
indexed (or --ingest). The pipeline is called in-process — no API, no auth — exactly
as the chat endpoint calls it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from eval.harness.loader import load_golden_dataset, validate_dataset
from eval.harness.metrics import aggregate_results, errored, score_question
from eval.harness.reporter import format_console, write_report


def _collection_id(name: str, create: bool):
    from src.db.models import Collection
    from src.db.session import session_scope

    with session_scope() as db:
        col = db.query(Collection).filter(Collection.name == name).first()
        if col is None:
            if not create:
                raise SystemExit(f"Collection « {name} » introuvable (utilisez --ingest pour la créer).")
            col = Collection(name=name, description="Créée par le banc d'évaluation")
            db.add(col)
            db.flush()
        return col.id


def ingest_directory(collection: str, directory: Path) -> None:
    """Index every supported file of ``directory`` into ``collection`` (dedup by hash)."""
    from sqlalchemy import select

    from src.db.models import Document
    from src.db.session import session_scope
    from src.ingestion import storage
    from src.ingestion.filetypes import extension_of, mime_for
    from src.ingestion.pipeline import ingest_document

    collection_id = _collection_id(collection, create=True)
    for path in sorted(directory.iterdir()):
        mime = mime_for(path.name)
        if not path.is_file() or mime is None:
            continue
        with path.open("rb") as fh:
            staged = storage.stage_stream(fh, max_bytes=10**10)
        with session_scope() as db:
            existing = db.execute(
                select(Document).where(Document.collection_id == collection_id, Document.sha256 == staged.sha256)
            ).scalar_one_or_none()
            if existing is not None and existing.status == "ready":
                staged.path.unlink(missing_ok=True)
                print(f"déjà indexé : {path.name}")
                continue
            doc = existing or Document(collection_id=collection_id, filename=path.name, mime_type=mime,
                                       size_bytes=staged.size, sha256=staged.sha256)
            db.add(doc)
            db.flush()
            destination = storage.commit_staged(staged, storage.final_path(collection_id, doc.id, extension_of(path.name)))
            doc.storage_path = storage.relative_to_root(destination)
            document_id = doc.id
        result = ingest_document(document_id)
        print(f"indexé : {path.name} — {result.chunk_count} chunks en {result.seconds}s")


async def run_evaluation(dataset_path: str, collection: str, output_dir: str, threshold: float, model: str | None) -> Path:
    from src.config import get_settings
    from src.llm.factory import resolve_model
    from src.rag.rag_pipeline import answer_question
    from src.retrieval.embeddings import get_embeddings

    settings = get_settings()
    ds = load_golden_dataset(dataset_path)
    errors = validate_dataset(ds, settings.no_answer_text)
    if errors:
        raise SystemExit("Jeu de référence invalide :\n" + "\n".join(errors))

    collection_id = _collection_id(collection, create=False)
    spec = resolve_model(model)
    embeddings = get_embeddings()
    results = []
    for index, q in enumerate(ds.questions, start=1):
        print(f"[{index}/{len(ds.questions)}] {q.id} {q.category} …", flush=True)
        try:
            response = (await answer_question(q.question, [collection_id], model_id=spec.id)).as_dict()
            results.append(score_question(q, response, no_answer=settings.no_answer_text, embeddings=embeddings, threshold=threshold))
        except Exception as exc:  # one failing question must not abort the run
            results.append(errored(q, f"{type(exc).__name__}: {exc}"))

    run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    summary = {
        "run_id": run_id,
        "dataset": dataset_path,
        "dataset_version": ds.metadata.get("version"),
        "collection": collection,
        "model": spec.id,
        "embedding_model": settings.embedding_model,
        "thresholds": {"semantic": threshold},
        "retrieval": {
            "max_distance": settings.retrieval_max_distance,
            "top_k": settings.retrieval_top_k,
            "candidates": settings.retrieval_candidates,
            "hybrid": settings.retrieval_hybrid,
            "reranker": settings.reranker,
            "chunk_size": settings.chunk_size,
        },
        "results": aggregate_results(results),
    }
    out = write_report(results, summary, Path(output_dir) / run_id)
    print()
    print(format_console(summary))
    print(f"\nRapport : {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Banc d'évaluation ContextHub")
    parser.add_argument("--dataset", default="eval/datasets/golden_mifid.json")
    parser.add_argument("--collection", default=None, help="défaut : metadata.collection du jeu")
    parser.add_argument("--output", default="eval/results")
    parser.add_argument("--threshold", type=float, default=0.75, help="seuil de similarité sémantique")
    parser.add_argument("--model", default=None, help="provider:modele (doit figurer dans LLM_MODELS)")
    parser.add_argument("--ingest", type=Path, default=None, help="dossier de documents à indexer avant l'évaluation")
    args = parser.parse_args(argv)

    collection = args.collection or load_golden_dataset(args.dataset).metadata.get("collection") or "default"
    if args.ingest:
        ingest_directory(collection, args.ingest)
    asyncio.run(run_evaluation(args.dataset, collection, args.output, args.threshold, args.model))
    return 0


if __name__ == "__main__":
    sys.exit(main())

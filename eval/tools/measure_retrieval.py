"""Retrieval-only measurement on a golden dataset that names the expected pages.

    python -m eval.tools.measure_retrieval --dataset eval/datasets/golden_manuals.json --corpus eval/corpora/manuals
    python -m eval.tools.measure_retrieval --dataset eval/datasets/golden_manuals.json --collection manuels

Each question goes through the ``retrieve`` node of the pipeline — the very search,
threshold, filters and neighbours the chat uses — and nothing else: no planner, no LLM. What
is measured is therefore what the model would be given to read.

``--rewrites FILE`` (JSON ``{"question id": ["rewording", ...]}``) also replays the ``plan``
node with those rewordings, as if the planner had written them (a scripted stand-in replaces
the LLM; sub-queries off): the rewording mechanism is measured with *given* rewordings, not
with what a real LLM would write — the file says who wrote them.

``--corpus DIR`` indexes the files of DIR in an in-process SQLite database and an
in-memory chunk store, with the configured embedding model and OCR, and leaves the
configured database untouched (the lexical half of the search is then the in-memory
approximation, not PostgreSQL full-text search). ``--collection NAME`` measures an
already indexed collection of the configured database (PostgreSQL in production).

Per question: expected document among the passages (doc_hit), expected page among the
passages of that document (page_hit: page where a passage starts — the cited page;
page_hit_span: any page a passage runs over, when the index records where it ends), rank of the first such passage among the passages the
search kept, neighbours aside (reciprocal rank),
share of passages from documents about another product (``product_documents`` in the
dataset), size of the context, and how the question ended at retrieval (refused without
LLM, refused with a product notice, or passed to the LLM). Also: share of the expected pages
the context covers (page_recall), expected section among the context's (section_hit, from
``reference_article``), and — for questions with ``expected_steps`` — share of the steps whose
terms the context contains (steps_in_context: what a complete procedure answer needs to read).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_corpus(directory: Path, expected: dict[str, str]) -> list[str]:
    """Problems with the corpus files named in the dataset (missing, other edition)."""
    problems = []
    for name, sha in expected.items():
        path = directory / name
        if not path.is_file():
            problems.append(f"fichier absent : {path}")
        elif _sha256(path) != sha:
            problems.append(f"{name} : empreinte SHA-256 différente de celle du jeu (autre édition ?)")
    return problems


def _in_process_environment() -> None:
    """SQLite in memory + in-memory chunk store, before any ``src`` module reads settings."""
    os.environ["DATABASE_URL"] = "sqlite://"
    os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="contexthub-measure-"))
    from src.config import reset_settings_cache

    reset_settings_cache()
    from src.db.models import Base
    from src.db.session import configure_engine, get_engine
    from src.retrieval import store

    configure_engine("sqlite://")
    Base.metadata.create_all(get_engine())
    if store._store_override is None:  # noqa: SLF001 - a caller may have set its own
        store.set_store_override(store.InMemoryChunkStore())


def _page_of(source_or_chunk) -> int | None:
    meta = getattr(source_or_chunk, "metadata", None) or {}
    return meta.get("page")


def _covers(chunk, pages: set[int]) -> bool:
    """Whether the passage runs over one of ``pages`` (from its first to its last page)."""
    meta = chunk.metadata or {}
    first = meta.get("page")
    if not pages:
        return True
    if first is None:
        return False
    return any(page in pages for page in range(first, (meta.get("page_end") or first) + 1))


def score(question: dict, state: dict, no_answer: str) -> dict:
    context = state.get("context") or []
    answer = state.get("answer") or ""
    expected_doc = question.get("source_document")
    expected_docs = [d.strip() for d in (expected_doc or "").split(";") if d.strip()]
    pages = set(question.get("expected_pages") or [])
    product_docs = question.get("product_documents")
    names = [(c.metadata or {}).get("filename") for c in context]
    row = {
        "id": question["id"],
        "category": question["category"],
        "answerable": bool(question["answerable"]),
        "passages": len(context),
        "context_chars": sum(len(c.content) for c in context),
        "rewrites": state.get("search_rewrites") or [],
        "outcome": "refused" if answer.strip() == no_answer.strip()
        else "notice" if answer.startswith(no_answer.strip())
        else "llm",
        "answer": answer if answer else None,
        "pages": [f"{(c.metadata or {}).get('filename')}#{_page_of(c)}" for c in context],
    }
    if expected_docs:
        row["doc_hit"] = all(doc in names for doc in expected_docs)
        expected = [c for c in context if (c.metadata or {}).get("filename") in expected_docs]
        row["page_hit"] = any(not pages or _page_of(c) in pages for c in expected)
        # Passages that start on an earlier page and run over an expected one (page_end).
        row["page_hit_span"] = any(_covers(c, pages) for c in expected)
        if pages:
            covered = {page for page in pages if any(_covers(c, {page}) for c in expected)}
            row["page_recall"] = round(len(covered) / len(pages), 4)
        # Rank among the passages the search kept (neighbours joined around them excluded).
        ranked = state.get("anchors") or context
        rank = next(
            (
                i
                for i, c in enumerate(ranked, start=1)
                if (c.metadata or {}).get("filename") in expected_docs and (not pages or _page_of(c) in pages)
            ),
            None,
        )
        row["first_rank"] = rank
        row["reciprocal_rank"] = round(1.0 / rank, 4) if rank else 0.0
    if question.get("reference_article") and question.get("answerable"):
        from eval.harness.metrics import article_hit

        sections = [{"section": (c.metadata or {}).get("section"), "section_path": (c.metadata or {}).get("section_path")}
                    for c in context]
        row["section_hit"] = bool(article_hit(sections, question["reference_article"]))
    steps = question.get("expected_steps")
    if steps:
        from eval.harness.metrics import steps_found

        found = steps_found(steps, "\n".join(c.content for c in context))
        row["steps_in_context"] = round(sum(found) / len(found), 4)
        row["steps_missing"] = [step.get("label") for step, ok in zip(steps, found, strict=True) if not ok]
    if product_docs is not None and context:
        row["other_product_share"] = round(sum(1 for n in names if n not in product_docs) / len(context), 3)
    refs = question.get("expected_notice_refs")
    if refs:
        row["notice_ok"] = row["outcome"] == "notice" and all(r in answer for r in refs)
    return row


def _rate(values) -> float | None:
    kept = [v for v in values if v is not None]
    return round(sum(1 for v in kept if v) / len(kept), 4) if kept else None


def _mean(values) -> float | None:
    kept = [v for v in values if v is not None]
    return round(sum(kept) / len(kept), 4) if kept else None


def aggregate(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    traps = [r for r in rows if not r["answerable"]]
    return {
        "questions": len(rows),
        "doc_hit": _rate(r.get("doc_hit") for r in answerable),
        "page_hit": _rate(r.get("page_hit") for r in answerable),
        "page_hit_span": _rate(r.get("page_hit_span") for r in answerable),
        "page_recall": _mean(r.get("page_recall") for r in answerable),
        "section_hit": _rate(r.get("section_hit") for r in answerable),
        "steps_in_context": _mean(r.get("steps_in_context") for r in answerable),
        "mrr": _mean(r.get("reciprocal_rank") for r in answerable),
        "other_product_share": _mean(r.get("other_product_share") for r in rows),
        "context_chars_mean": _mean(r["context_chars"] for r in rows),
        "traps_refused_or_notice": _rate(r["outcome"] != "llm" for r in traps),
        "notices_expected_ok": _rate(r.get("notice_ok") for r in traps if "notice_ok" in r),
    }


def format_table(rows: list[dict]) -> str:
    lines = [
        f"{'id':7} {'doc':>4} {'page':>4} {'pages':>5} {'sect.':>5} {'étap.':>5} {'rang':>4} {'autre':>6} "
        f"{'pass.':>5} {'car.':>6}  issue"
    ]
    for r in rows:
        doc = "" if "doc_hit" not in r else ("oui" if r["doc_hit"] else "NON")
        page = "" if "page_hit" not in r else ("oui" if r["page_hit"] else "NON")
        rank = r.get("first_rank") or ""
        other = "" if r.get("other_product_share") is None else f"{r['other_product_share']:.2f}"
        recall = "" if r.get("page_recall") is None else f"{r['page_recall']:.2f}"
        section = "" if "section_hit" not in r else ("oui" if r["section_hit"] else "NON")
        steps = "" if r.get("steps_in_context") is None else f"{r['steps_in_context']:.2f}"
        lines.append(
            f"{r['id']:7} {doc:>4} {page:>4} {recall:>5} {section:>5} {steps:>5} {rank!s:>4} {other:>6} "
            f"{r['passages']:>5} {r['context_chars']:>6}  {r['outcome']}"
        )
    return "\n".join(lines)


def _scripted_planner(rewrites: list[str]):
    """Stands in for the LLM in the ``plan`` node: replies with the given rewordings."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    return FakeListChatModel(responses=[json.dumps({"queries": [], "rewrites": rewrites}, ensure_ascii=False)])


async def measure(dataset: dict, collection: str, rewrites: dict[str, list[str]] | None = None) -> list[dict]:
    from src.config import get_settings
    from src.db.models import Collection
    from src.db.session import session_scope
    from src.llm import llm_chain
    from src.rag.rag_pipeline import plan, retrieve

    with session_scope() as db:
        col = db.query(Collection).filter(Collection.name == collection).first()
        if col is None:
            raise SystemExit(f"Collection « {collection} » introuvable.")
        collection_id = str(col.id)
    settings = get_settings()
    no_answer = settings.no_answer_text
    saved = settings.agentic_retrieval, settings.query_rewrite
    if rewrites:
        # The rewording mechanism alone: no sub-query (nothing would write them).
        settings.agentic_retrieval, settings.query_rewrite = False, True
    rows = []
    try:
        for question in dataset["questions"]:
            state = await retrieve({"question": question["question"], "collection_ids": [collection_id]})
            given = (rewrites or {}).get(question["id"])
            if given:
                previous = llm_chain._llm_override  # noqa: SLF001 - a caller may have set its own
                llm_chain.set_llm_override(_scripted_planner(given))
                try:
                    state = {**state, **await plan({**state, "question": question["question"],
                                                    "collection_ids": [collection_id], "model_id": "ollama:scripted"})}
                finally:
                    llm_chain.set_llm_override(previous)
            rows.append(score(question, state, no_answer))
    finally:
        settings.agentic_retrieval, settings.query_rewrite = saved
    return rows


def _settings_snapshot() -> dict:
    from src.config import get_settings

    settings = get_settings()
    return {name: getattr(settings, name) for name in (
        "retrieval_max_distance", "retrieval_top_k", "retrieval_candidates", "retrieval_hybrid", "reranker",
        "retrieval_neighbours", "retrieval_neighbour_scope", "retrieval_context_chars",
    )}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mesure de la récupération seule (sans LLM)")
    parser.add_argument("--dataset", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--corpus", type=Path, help="indexer ce dossier en mémoire (base configurée intacte)")
    source.add_argument("--collection", help="collection déjà indexée de la base configurée")
    parser.add_argument("--output", default="eval/results", help="dossier des rapports")
    parser.add_argument("--label", default="", help="libellé ajouté au nom du rapport")
    parser.add_argument(
        "--rewrites", type=Path,
        help="JSON {id: [reformulation, ...]} rejouées par le nœud plan (LLM remplacé ; reformulations données)",
    )
    parser.add_argument(
        "--explain", action="append", default=[], metavar="ID",
        help="afficher aussi le diagnostic de récupération (explain-retrieval) de cette question (répétable)",
    )
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    metadata = dataset.get("metadata", {})
    collection = args.collection or metadata.get("collection") or "default"
    if args.corpus or not args.collection:
        corpus = args.corpus or Path(metadata.get("corpus_dir", ""))
        problems = check_corpus(corpus, metadata.get("corpus_files") or {})
        if problems:
            raise SystemExit("Corpus incomplet :\n" + "\n".join(problems))
        _in_process_environment()
        from eval.run_eval import ingest_directory

        ingest_directory(collection, corpus)

    given = json.loads(args.rewrites.read_text(encoding="utf-8")) if args.rewrites else None
    if given is not None:
        given = {key: value for key, value in given.items() if not key.startswith("_")}
    rows = asyncio.run(measure(dataset, collection, given))
    for question in (q for q in dataset["questions"] if q["id"] in args.explain):
        from src import cli

        print(f"\n=== {question['id']}")
        cli.main(["explain-retrieval", "--collection", collection, "--limit", "25", question["question"]])
    summary = aggregate(rows)
    print(format_table(rows))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + (f"-{args.label}" if args.label else "")
    out = Path(args.output) / f"retrieval-{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    settings = _settings_snapshot()
    out.write_text(json.dumps({"dataset": args.dataset, "settings": settings, "rewrites": str(args.rewrites or ""),
                               "summary": summary, "rows": rows}, indent=2, ensure_ascii=False))
    print(f"Rapport : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

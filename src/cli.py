"""Operator command line.

    python -m src.cli create-admin --email admin@client.fr
    python -m src.cli verify-audit
    python -m src.cli purge
    python -m src.cli reindex [--collection NAME]
    python -m src.cli explain-retrieval --collection NAME "question" [--json] [--limit N] [--excerpt N]
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from sqlalchemy import select


def _create_admin(args) -> int:
    from src.audit import service as audit
    from src.auth.users import create_local_user, find_by_email
    from src.db.models import ROLE_ADMIN
    from src.db.session import session_scope

    password = args.password or getpass.getpass("Mot de passe : ")
    with session_scope() as db:
        existing = find_by_email(db, args.email)
        if existing:
            existing.role = ROLE_ADMIN
            print(f"{existing.email} est maintenant administrateur.")
        else:
            try:
                create_local_user(db, args.email, password, role=ROLE_ADMIN, full_name=args.name)
            except ValueError as exc:
                print(f"Erreur : {exc}", file=sys.stderr)
                return 1
            print(f"Administrateur {args.email} créé.")
    audit.record("user.bootstrap_admin", details={"email": args.email.lower(), "via": "cli"})
    return 0


def _verify_audit(_args) -> int:
    from src.audit.service import verify_chain
    from src.db.session import SessionLocal

    db = SessionLocal()
    try:
        report = verify_chain(db)
    finally:
        db.close()
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    return 0 if report.valid else 2


def _purge(_args) -> int:
    from src.maintenance import purge_expired_data

    print(json.dumps(purge_expired_data(), indent=2))
    return 0


def _reindex(args) -> int:
    from src.db.models import Collection, Document
    from src.db.session import SessionLocal
    from src.ingestion.filetypes import IngestionError
    from src.ingestion.pipeline import ingest_document, mark_failed

    db = SessionLocal()
    try:
        stmt = select(Document.id, Document.filename).where(
            (Document.storage_path.is_not(None)) | (Document.source_url.is_not(None))
        )
        if args.collection:
            stmt = stmt.join(Collection).where(Collection.name == args.collection)
        documents = db.execute(stmt).all()
    finally:
        db.close()
    failures = 0
    for document_id, filename in documents:
        try:
            result = ingest_document(document_id)
            print(f"OK   {filename} — {result.chunk_count} chunks")
        except IngestionError as exc:
            failures += 1
            mark_failed(document_id, str(exc))
            print(f"FAIL {filename} — {exc}")
    print(f"{len(documents) - failures}/{len(documents)} documents réindexés.")
    return 1 if failures else 0


_STATUS_LABELS = {"kept": "retenu", "excluded": "exclu", "neighbour": "voisin"}


def _product_cell(row: dict) -> str:
    if row["product"] == "match":
        label = "= " + ", ".join(row["matched_refs"][:2])
    elif row["product"] == "other":
        label = "autre"
    else:
        label = None
    if row["via_reference"]:
        label = f"{label} (réf.)"
    return label


def _cell(value, width: int, align: str = "<") -> str:
    text = "-" if value is None else str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{text:{align}{width}}"


def _explain_retrieval(args) -> int:
    import asyncio
    import uuid

    from src.config import get_settings
    from src.db.models import Collection
    from src.db.session import SessionLocal
    from src.rag.retrieval import describe, retrieve_context

    db = SessionLocal()
    try:
        ids: list[uuid.UUID] = []
        for wanted in args.collection:
            query = db.query(Collection).filter(Collection.conversation_id.is_(None))
            try:
                found = query.filter(Collection.id == uuid.UUID(wanted)).first()
            except ValueError:
                found = query.filter(Collection.name == wanted).first()
            if found is None:
                print(f"Collection introuvable : {wanted}", file=sys.stderr)
                return 1
            ids.append(found.id)
    finally:
        db.close()

    settings = get_settings()
    result = asyncio.run(retrieve_context(args.question, ids))
    rows = describe(result, excerpt=args.excerpt)
    if args.json:
        print(json.dumps({"question": args.question, "settings": {
            "retrieval_max_distance": settings.retrieval_max_distance,
            "retrieval_top_k": settings.retrieval_top_k,
            "retrieval_candidates": settings.retrieval_candidates,
            "retrieval_hybrid": settings.retrieval_hybrid,
            "reranker": settings.reranker,
            "retrieval_reference_boost": settings.retrieval_reference_boost,
            "retrieval_neighbours": settings.retrieval_neighbours,
            "retrieval_context_chars": settings.retrieval_context_chars,
        }, "asked_refs": result.asked_refs, "notice": result.notice, "candidates": rows}, indent=2, ensure_ascii=False))
        return 0

    kept = sum(1 for row in rows if row["status"] == "kept")
    neighbours = sum(1 for row in rows if row["status"] == "neighbour")
    print(f"Question : {args.question}")
    print(
        f"Réglages : seuil de distance {settings.retrieval_max_distance:.2f}, top_k {settings.retrieval_top_k}, "
        f"{settings.retrieval_candidates} candidats par liste, hybride {'oui' if settings.retrieval_hybrid else 'non'}, "
        f"re-classement {settings.reranker}"
    )
    print(
        "Recherche de la question telle quelle : sans reformulation (historique) ni sous-requêtes du planificateur."
    )
    if result.asked_refs:
        carrying = len(result.document_refs)
        print(
            f"Références citées : {', '.join(result.asked_refs)} — {carrying} document(s) avec des références "
            "dans les collections ; « = » : le document porte la référence, « autre » : il en porte d'autres, "
            "« (réf.) » : trouvé par la recherche limitée aux documents qui la portent."
        )
    if result.notice:
        print(f"Garde-fou produit (réponse sans appel au LLM) : {result.notice}")
    print(
        f"{len(rows) - neighbours} candidats, {kept} retenus, {neighbours} voisin(s) joint(s) au contexte "
        f"({sum(len(c.content) for c in result.context)} caractères, budget {settings.retrieval_context_chars}).\n"
    )
    header = (
        f"{_cell('rang', 4)} {_cell('ctx', 3)} {_cell('statut', 7)} {_cell('distance', 8, '>')} {_cell('vect.', 5, '>')} "
        f"{_cell('lex.', 5, '>')} {_cell('score lex', 9, '>')} {_cell('RRF', 7, '>')} {_cell('document', 34)} "
        f"{_cell('page', 4, '>')} {_cell('section', 30)} {_cell('produit', 18)} raison"
    )
    print(header)
    print("-" * len(header))
    for row in rows[: args.limit] if args.limit else rows:
        distance, fused = f"{row['distance']:.3f}", f"{row['fused_score']:.4f}"
        print(
            f"{_cell(row['final_rank'], 4)} {_cell(row['context_position'], 3)} "
            f"{_cell(_STATUS_LABELS.get(row['status'], row['status']), 7)} "
            f"{_cell(distance, 8, '>')} {_cell(row['vector_rank'], 5, '>')} "
            f"{_cell(row['lexical_rank'], 5, '>')} {_cell(row['lexical_score'], 9, '>')} "
            f"{_cell(fused, 7, '>')} {_cell(row['document'], 34)} "
            f"{_cell(row['page'], 4, '>')} {_cell(row['section'], 30)} {_cell(_product_cell(row), 18)} "
            f"{row['reason'] or ''}"
        )
        if row.get("excerpt"):
            print(f"     « {row['excerpt']} »")
    if args.limit and len(rows) > args.limit:
        print(f"… {len(rows) - args.limit} autres candidats (--limit 0 pour tout afficher)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description="ContextHub — administration")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="créer (ou promouvoir) un administrateur")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrateur")
    p.add_argument("--password", help="déconseillé (historique du shell) ; demandé si absent")
    p.set_defaults(func=_create_admin)

    sub.add_parser("verify-audit", help="vérifier l'intégrité du journal d'audit").set_defaults(func=_verify_audit)
    sub.add_parser("purge", help="appliquer les durées de conservation").set_defaults(func=_purge)

    p = sub.add_parser("reindex", help="réindexer les documents (après changement de modèle d'embedding)")
    p.add_argument("--collection")
    p.set_defaults(func=_reindex)

    p = sub.add_parser(
        "explain-retrieval",
        help="diagnostic : candidats d'une question, distance, rangs, passages retenus et raisons d'exclusion",
    )
    p.add_argument("question")
    p.add_argument("--collection", action="append", required=True, help="nom ou UUID (répétable)")
    p.add_argument("--json", action="store_true", help="sortie JSON")
    p.add_argument("--limit", type=int, default=30, help="lignes affichées (0 : toutes)")
    p.add_argument("--excerpt", type=int, default=0, help="afficher N caractères de chaque passage")
    p.set_defaults(func=_explain_retrieval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

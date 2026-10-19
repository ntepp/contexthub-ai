"""Operator command line.

    python -m src.cli create-admin --email admin@client.fr
    python -m src.cli verify-audit
    python -m src.cli purge
    python -m src.cli reindex [--collection NAME]
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

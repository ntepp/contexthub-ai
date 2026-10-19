"""Integration tests against a real PostgreSQL + pgvector (Alembic schema, hybrid SQL).

    TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/contexthub_test pytest -m pg

The database is wiped and migrated from scratch. Skipped when the variable is unset.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from src.db.session import configure_engine
from src.retrieval import store as store_module
from tests.conftest import grant, upload
from tests.fakes import DIM, HashEmbeddings, make_pdf

PG_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.pg, pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL non défini")]


@pytest.fixture(scope="module")
def migrated():
    from alembic.config import Config

    from alembic import command

    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    engine.dispose()
    os.environ["EMBEDDING_DIM"] = str(DIM)
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = PG_URL
    command.upgrade(cfg, "head")
    yield
    os.environ.pop("EMBEDDING_DIM", None)


@pytest.fixture
def pg(migrated):
    engine = configure_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE hub_audit_events, hub_messages, hub_conversations, hub_chunks, hub_documents, "
                "hub_collection_grants, hub_api_keys, hub_collections, hub_users RESTART IDENTITY CASCADE"
            )
        )
    store_module.set_store_override(None)  # real PgChunkStore
    yield engine
    configure_engine("sqlite://")  # hand back to the autouse SQLite fixture for teardown
    from src.db.models import Base
    from src.db.session import get_engine

    Base.metadata.create_all(get_engine())


PDF = make_pdf(
    [
        ["Article 93", "Transposition", "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."],
        ["Article 4", "Definitions", "Un systeme organise de negociation est un systeme multilateral."],
        ["Article 12", "Agrement", "Les autorites competentes accordent l agrement aux entreprises d investissement."],
    ]
)


def _admin_client(pg):
    from tests.conftest import _client, _make_user, login

    user = _make_user("admin@banque.fr", "admin")
    client = _client()
    assert login(client, user.email).status_code == 200
    return client, user


def test_migration_created_v1_schema(pg):
    with pg.connect() as conn:
        tables = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
        assert {"hub_users", "hub_chunks", "hub_audit_events", "hub_conversations", "hub_collection_grants"} <= tables
        indexes = set(conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='hub_chunks'")).scalars())
        assert {"ix_hub_chunks_embedding", "ix_hub_chunks_tsv"} <= indexes


def test_end_to_end_upload_ask_delete_on_postgres(pg):
    from tests.conftest import _client, _make_user, login

    admin, _ = _admin_client(pg)
    member_user = _make_user("analyste@banque.fr", "member")
    member = _client()
    login(member, member_user.email)

    collection = admin.post("/collections", json={"name": "UE"}).json()
    doc = upload(admin, collection["id"], "directive.pdf", PDF).json()
    assert admin.get(f"/documents/{doc['id']}").json()["status"] == "ready"
    grant(admin, collection["id"], member_user.id)

    body = member.post("/chat/ask", json={"question": "A partir de quelle date les Etats membres appliquent ces dispositions ?"}).json()
    assert body["refused"] is False, body
    assert body["sources"][0]["section"] == "Article 93 — Transposition"
    assert body["sources"][0]["page"] == 1

    with pg.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM hub_chunks")).scalar_one() > 0
    assert admin.delete(f"/collections/{collection['id']}").status_code == 200
    with pg.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM hub_chunks")).scalar_one() == 0
    assert admin.get("/admin/audit/verify").json()["valid"] is True


def test_hybrid_search_reaches_lexical_matches_beyond_vector_top_n(pg):
    admin, _ = _admin_client(pg)
    collection = admin.post("/collections", json={"name": "UE"}).json()
    upload(admin, collection["id"], "directive.pdf", PDF)
    cid = uuid.UUID(collection["id"])
    store = store_module.get_store()
    query = "agrement article 12"
    vector = HashEmbeddings().embed_query(query)

    vector_only = store.search(query, vector, [cid], candidates=1, hybrid=False)
    hybrid = store.search(query, vector, [cid], candidates=1, hybrid=True)
    assert len(vector_only) == 1
    assert len(hybrid) >= len(vector_only)
    assert any("Article 12" in (c.metadata.get("section") or "") for c in hybrid)
    assert all(0.0 <= c.distance <= 2.0 for c in hybrid)

    other = admin.post("/collections", json={"name": "Autre"}).json()
    assert store.search(query, vector, [uuid.UUID(other["id"])], candidates=5, hybrid=True) == []

from __future__ import annotations

import os
import tempfile

# Configure the environment before any `src` import reads settings.
_UPLOADS = tempfile.mkdtemp(prefix="contexthub-test-uploads-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": "sqlite://",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-0123456789",
        "UPLOAD_DIR": _UPLOADS,
        "LLM_MODELS": "ollama:fake-model,anthropic:claude-sonnet-5-5",
        "OCR_ENABLED": "false",
        "ADMIN_EMAIL": "",
        "ADMIN_PASSWORD": "",
        "LANGCHAIN_TRACING_V2": "false",
        "RETRIEVAL_MAX_DISTANCE": "0.75",
        "EMBED_BATCH_SIZE": "4",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.config import reset_settings_cache  # noqa: E402

reset_settings_cache()

from src.app import app  # noqa: E402
from src.auth.rate_limit import limiter  # noqa: E402
from src.auth.users import create_local_user  # noqa: E402
from src.db.models import ROLE_ADMIN, ROLE_MEMBER, Base  # noqa: E402
from src.db.session import SessionLocal, configure_engine, get_engine  # noqa: E402
from src.llm import llm_chain  # noqa: E402
from src.retrieval import embeddings as embeddings_module  # noqa: E402
from src.retrieval import store as store_module  # noqa: E402
from src.workers.celery_app import celery_app  # noqa: E402
from tests.fakes import HashEmbeddings, ScriptedChatModel, is_condense_prompt  # noqa: E402

celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True
# Never touch a real Redis from the test-suite.
limiter._redis_disabled_until = float("inf")

PASSWORD = "Correct-Horse-42!"


def default_responder(messages) -> str:
    if is_condense_prompt(messages):
        return "Question autonome reformulee"
    return "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017 [1]."


@pytest.fixture(autouse=True)
def _environment():
    configure_engine("sqlite://")
    Base.metadata.create_all(get_engine())
    limiter.clear_memory()
    store = store_module.InMemoryChunkStore()
    store_module.set_store_override(store)
    embeddings_module.set_embeddings_override(HashEmbeddings())
    llm = ScriptedChatModel(responder=default_responder)
    llm.calls = []
    llm_chain.set_llm_override(llm)
    yield
    Base.metadata.drop_all(get_engine())
    store_module.set_store_override(None)
    embeddings_module.set_embeddings_override(None)
    llm_chain.set_llm_override(None)


@pytest.fixture
def store() -> store_module.InMemoryChunkStore:
    return store_module.get_store()  # type: ignore[return-value]


@pytest.fixture
def llm() -> ScriptedChatModel:
    return llm_chain._llm_override


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _make_user(email: str, role: str):
    session = SessionLocal()
    try:
        user = create_local_user(session, email, PASSWORD, role=role, full_name=email.split("@")[0])
        session.commit()
        return user
    finally:
        session.close()


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "contexthub"})


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


@pytest.fixture
def anon() -> TestClient:
    return _client()


@pytest.fixture
def admin_user():
    return _make_user("admin@banque.fr", ROLE_ADMIN)


@pytest.fixture
def member_user():
    return _make_user("analyste@banque.fr", ROLE_MEMBER)


@pytest.fixture
def admin(admin_user) -> TestClient:
    client = _client()
    assert login(client, admin_user.email).status_code == 200
    return client


@pytest.fixture
def member(member_user) -> TestClient:
    client = _client()
    assert login(client, member_user.email).status_code == 200
    return client


@pytest.fixture
def collection(admin) -> dict:
    resp = admin.post("/collections", json={"name": "Reglementation UE", "description": "MiFID"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def grant(admin: TestClient, collection_id: str, user_id, level: str = "viewer") -> None:
    resp = admin.put(f"/admin/collections/{collection_id}/grants", json={"user_id": str(user_id), "level": level})
    assert resp.status_code == 200, resp.text


def upload(client: TestClient, collection_id: str, filename: str, content: bytes, mime: str = "application/pdf"):
    return client.post(
        f"/collections/{collection_id}/documents",
        files={"file": (filename, content, mime)},
    )

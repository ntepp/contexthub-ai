"""
ContextHub AI — Enterprise Retrieval Copilot
Streamlit demo UI — connects to the FastAPI backend on localhost:8000
Run: streamlit run demo_ui.py
"""

import time

import requests
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="ContextHub AI",
    page_icon="🧠",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — Architecture stack + live operational metrics
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🧠 ContextHub AI")
    st.caption("Enterprise Retrieval Copilot")
    st.divider()

    st.subheader("System Architecture")
    st.markdown(
        """
- **FastAPI** — API Gateway
- **Celery + Redis** — Async ingestion queue
- **pgvector** — Persistent vector store
- **bge-m3** — Multilingual embeddings
- **LangGraph** — Retrieval orchestration
- **Ollama / OpenAI** — LLM abstraction layer
"""
    )
    st.divider()

    st.subheader("Observability")
    st.markdown(
        "[🔍 LangSmith Traces](https://smith.langchain.com)"
        " → project **contexthub-ai**"
    )
    st.divider()

    st.subheader("Operational Metrics")
    try:
        metrics = requests.get(f"{API_BASE}/metrics", timeout=3).json()
        st.metric("LLM Provider", metrics.get("llm_provider", "—").upper())
        st.metric("Model", metrics.get("llm_model", "—"))
        st.metric("Vector Store", metrics.get("vector_store", "—"))
        st.metric("Async Ingestion", metrics.get("async_ingestion", "—"))
        status = metrics.get("status", "unknown")
        if status == "operational":
            st.success("● System operational")
        else:
            st.warning(f"● {status}")
    except Exception:
        st.error("Backend unreachable — start FastAPI on :8000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_collections() -> list[str]:
    try:
        data = requests.get(f"{API_BASE}/collections", timeout=5).json()
        names = [c["name"] for c in data]
        return names if names else ["default"]
    except Exception:
        return ["default"]


def _poll_task(task_id: str, max_wait: int = 300) -> dict:
    """Poll GET /tasks/{id} until terminal state or timeout."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            resp = requests.get(f"{API_BASE}/tasks/{task_id}", timeout=5).json()
            if resp.get("status") in ("SUCCESS", "FAILURE"):
                return resp
        except Exception:
            pass
        time.sleep(2)
    return {"status": "TIMEOUT"}


# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------

tab_kb, tab_ask = st.tabs(["📄 Knowledge Base", "💬 Ask"])


# ── Tab 1 : Upload PDF ───────────────────────────────────────────────────────

with tab_kb:
    st.header("Document Ingestion")
    st.caption(
        "Upload a PDF — ingestion is asynchronous via Celery. "
        "The task is polled until the document is indexed."
    )

    collections = _get_collections()
    col_select = st.selectbox("Target collection", collections, key="kb_collection")

    uploaded_file = st.file_uploader("Select a PDF", type=["pdf"])

    if st.button("Upload & Ingest", disabled=uploaded_file is None):
        with st.spinner("Uploading…"):
            try:
                resp = requests.post(
                    f"{API_BASE}/upload",
                    params={"collection": col_select},
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    timeout=120,
                )
                resp.raise_for_status()
                task_data = resp.json()
                task_id = task_data["task_id"]
                st.info(f"Task queued — ID: `{task_id}`")
            except Exception as e:
                st.error(f"Upload failed: {e}")
                st.stop()

        with st.spinner("Indexing document (polling Celery task)…"):
            result = _poll_task(task_id)

        status = result.get("status")
        if status == "SUCCESS":
            st.success(f"Document indexed successfully into **{col_select}**.")
        elif status == "FAILURE":
            st.error(f"Ingestion failed: {result.get('error', 'unknown error')}")
        else:
            st.warning("Task timed out — check Celery worker logs.")

    st.divider()
    st.subheader("Indexed Documents")
    try:
        docs = requests.get(
            f"{API_BASE}/documents", params={"collection": col_select}, timeout=5
        ).json()
        if docs:
            for d in docs:
                badge = "✅" if d["status"] == "indexed" else "⏳"
                st.markdown(
                    f"{badge} **{d['filename']}** — {d['chunk_count'] or '—'} chunks — `{d['status']}`"
                )
        else:
            st.caption("No documents in this collection yet.")
    except Exception:
        st.caption("Could not load document list.")


# ── Tab 2 : Ask Question ─────────────────────────────────────────────────────

with tab_ask:
    st.header("Grounded Question Answering")
    st.caption(
        "Questions are answered strictly from indexed documents. "
        "Answers not supported by context are flagged as unavailable."
    )

    collections_ask = _get_collections()
    col_ask = st.selectbox("Knowledge base (collection)", collections_ask, key="ask_collection")

    question = st.text_input("Your question", placeholder="What does the document say about…?")

    if st.button("Ask", disabled=not question.strip()):
        with st.spinner("Retrieving and generating answer…"):
            try:
                resp = requests.post(
                    f"{API_BASE}/ask",
                    json={"question": question, "collection": col_ask},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                st.error(f"Request failed: {e}")
                st.stop()

        st.subheader("Answer")
        st.success(data.get("answer", "No answer returned."))

        m1, m2 = st.columns(2)
        confidence = data.get("confidence_metric", 0.0)
        latency = data.get("latency_seconds", 0.0)
        m1.metric("Retrieval Confidence", f"{confidence:.3f}")
        m2.metric("End-to-end Latency", f"{latency} s")

        sources = data.get("sources", [])
        if sources:
            with st.expander(f"Sources ({len(sources)})"):
                for s in sources:
                    page = s.get("page")
                    page_str = f", page {page}" if page is not None else ""
                    st.markdown(f"- `{s.get('source', 'unknown')}`{page_str}")
        else:
            st.caption("No source documents returned.")

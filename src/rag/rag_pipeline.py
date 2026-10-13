import json
import os
import time
from typing import AsyncGenerator

from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict

from src.cache.factory import cache_url, get_redis_client, is_url_cached
from src.data_loading.pdf_loader import PDFDataLoader
from src.data_loading.text_splitter import split_text
from src.data_loading.webpage_loader import WebDataLoader
from src.llm.llm_chain import initialize_model_llm
from src.utils.logging_config import get_logger
from src.utils.source_type import determine_source_type
from src.vector_store.embeddings import initialize_embeddings
from src.vector_store.factory import VectorStoreType, create_vector_store
from src.vector_store.pgvector import PGVectorConfig

log = get_logger()

embeddings = initialize_embeddings()

# langchain-postgres returns a cosine DISTANCE, not a similarity: 0.0 means
# identical, higher means less relevant, and results arrive sorted ascending.
# Chunks strictly above this distance are discarded.
MAX_DISTANCE = float(os.getenv("RETRIEVAL_MAX_DISTANCE", "0.75"))

# Number of candidate chunks pulled from pgvector before the distance filter is
# applied. This used to be langchain-postgres' implicit default of 4, which caps
# how much the distance threshold can ever do: on a question needing passages
# from two documents, 4 candidates is the binding constraint, not the threshold.
TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "4"))

# Emitted verbatim in two places: by `retrieve` when nothing clears the distance
# threshold, and by the LLM itself (injected into the prompt) when the retrieved
# context does not support an answer. Both paths must produce the exact same
# string — the evaluation harness matches on it, and the UI relies on it.
# It MUST be written in the language of the indexed corpus.
NO_ANSWER = os.getenv(
    "NO_ANSWER_TEXT",
    "L'information n'est pas disponible dans la base de connaissances actuelle.",
)

template = """You are an enterprise knowledge assistant.

Rules, in order of precedence:
1. If the context does not explicitly support an answer, reply with exactly the
   following sentence, alone, with nothing before or after it — no preamble, no
   confidence level:
   {no_answer}
2. Otherwise, answer only from the provided context, never from prior knowledge.
3. Always write your answer in the same language as the question.
4. Keep it concise, factual and professional, and end with a confidence level:
   High, Medium or Low.

Context:
{context}

Question:
{question}

Professional Answer:"""
prompt = PromptTemplate.from_template(template)


class State(TypedDict):
    question: str
    context: List[Document]
    answer: str
    sources: List[dict]
    collection: str
    confidence_metric: float


def _get_vector_store(collection: str = "default"):
    config = PGVectorConfig(collection_name=collection)
    return create_vector_store(embeddings, store_type=VectorStoreType.PGVECTOR, config=config)


def _build_graph():
    graph_builder = StateGraph(State).add_sequence([retrieve, generate])
    graph_builder.add_edge(START, "retrieve")
    return graph_builder.compile()


def retrieve(state: State):
    collection = state.get("collection", "default")
    vector_store = _get_vector_store(collection)
    results = vector_store.similarity_search_with_score(state["question"], k=TOP_K)
    kept = [(doc, dist) for doc, dist in results if dist <= MAX_DISTANCE]

    log.info("[retrieve] collection=%s question=%r", collection, state["question"])
    log.info(
        "[retrieve] %d/%d chunks retenus (top_k=%d, max_distance=%.2f) — distances: %s",
        len(kept),
        len(results),
        TOP_K,
        MAX_DISTANCE,
        ", ".join(f"{d:.3f}" for _, d in results) or "aucun resultat",
    )
    for doc, dist in kept[:3]:
        log.info(
            "[retrieve]   d=%.3f | %s p.%s | %s",
            dist,
            doc.metadata.get("source", "?"),
            doc.metadata.get("page", "?"),
            doc.page_content[:150].replace("\n", " "),
        )

    if not kept:
        log.warning("[retrieve] aucun chunk sous le seuil — court-circuit, le LLM ne sera pas appele")
        return {
            "context": [],
            "answer": NO_ANSWER,
            "sources": [],
            "confidence_metric": 0.0,
        }

    # Cosine distance runs to 2.0, so 1 - mean(distance) can go negative once
    # MAX_DISTANCE is raised above 1.0. Clamped: a negative "confidence" is
    # meaningless to the UI and to the report.
    top_distances = [dist for _, dist in kept[:3]]
    confidence = round(max(0.0, 1.0 - sum(top_distances) / len(top_distances)), 3)

    sources = [
        {"source": doc.metadata.get("source", "unknown"), "page": doc.metadata.get("page")}
        for doc, _ in kept
    ]
    return {
        "context": [doc for doc, _ in kept],
        "answer": None,
        "sources": sources,
        "confidence_metric": confidence,
    }


async def generate(state: State):
    if state.get("answer"):
        return {"answer": state["answer"]}
    docs_content = "\n\n".join(doc.page_content for doc in state["context"])
    messages = prompt.invoke(
        {"question": state["question"], "context": docs_content, "no_answer": NO_ANSWER}
    )
    llm = initialize_model_llm()

    log.info(
        "[generate] model=%s provider=%s | contexte: %d chunks, %d caracteres",
        os.getenv("LLM_MODEL_NAME", "?"),
        os.getenv("LLM_PROVIDER", "ollama"),
        len(state["context"]),
        len(docs_content),
    )

    start = time.time()
    full_response = ""
    chunk_count = 0
    async for chunk in llm.astream(messages):
        full_response += chunk.content
        chunk_count += 1

    elapsed = time.time() - start
    log.info("[generate] %d chunks recus en %.2fs (%d caracteres)", chunk_count, elapsed, len(full_response))
    if not full_response.strip():
        log.error("[generate] le modele n'a renvoye AUCUN contenu — reponse vide cote UI")
    else:
        log.info("[generate] reponse: %s", full_response)

    return {"answer": full_response}


def load_source(source: str, collection: str = "default") -> None:
    client = get_redis_client()
    cache_key = f"{collection}:{source}"

    if not is_url_cached(client, cache_key):
        source_type = determine_source_type(source)
        if source_type == "unknown":
            print(f"Source '{source}' is not a valid URL or PDF path.")
            return
        loader = PDFDataLoader(source) if source_type == "pdf" else WebDataLoader(url=source)
        docs = loader.load()
        all_splits = split_text(docs)

        vector_store = _get_vector_store(collection)
        BATCH_SIZE = 32
        total = len(all_splits)
        print(f"[ingestion] {total} chunks a indexer par lots de {BATCH_SIZE}")
        for i in range(0, total, BATCH_SIZE):
            batch = all_splits[i:i + BATCH_SIZE]
            vector_store.add_documents(documents=batch)
            print(f"[ingestion] lot {i // BATCH_SIZE + 1} : {min(i + BATCH_SIZE, total)}/{total}")
        cache_url(client, cache_key)
        print(f"Source '{source}' ingested into collection '{collection}'.")


async def stream_answer(question: str, collection: str = "default") -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events for a streaming chat response."""
    graph = _build_graph()
    sources = []
    streamed_chars = 0

    log.info("[stream] debut — collection=%s question=%r", collection, question)

    async for event in graph.astream_events(
        {"question": question, "collection": collection},
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            content = event["data"]["chunk"].content
            if content:
                streamed_chars += len(content)
                yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
        elif kind == "on_chain_end" and event.get("name") == "retrieve":
            output = event["data"].get("output", {})
            sources = output.get("sources", [])
            # When retrieve short-circuits, generate returns its answer without
            # invoking the LLM — no on_chat_model_stream event is ever emitted,
            # so the message has to be pushed to the client here or the UI would
            # render an empty bubble.
            fallback = output.get("answer")
            if fallback:
                streamed_chars += len(fallback)
                yield f"data: {json.dumps({'type': 'token', 'content': fallback})}\n\n"

    log.info("[stream] fin — %d caracteres envoyes, %d sources", streamed_chars, len(sources))
    if streamed_chars == 0:
        log.error("[stream] aucun caractere envoye au client — la bulle restera vide cote UI")

    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def create_and_run_graph(question: str, collection: str = "default") -> dict:
    graph = _build_graph()
    start = time.time()
    response = await graph.ainvoke({"question": question, "collection": collection})
    latency = round(time.time() - start, 2)
    return {
        "answer": response["answer"],
        "sources": response.get("sources", []),
        "confidence_metric": response.get("confidence_metric", 0.0),
        "latency_seconds": latency,
    }

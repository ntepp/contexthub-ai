import json
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
from src.utils.source_type import determine_source_type
from src.vector_store.embeddings import initialize_embeddings
from src.vector_store.factory import VectorStoreType, create_vector_store
from src.vector_store.pgvector import PGVectorConfig

embeddings = initialize_embeddings()

template = """You are an enterprise knowledge assistant.
Answer the user's question only from the provided context.
If the answer is not explicitly supported by the context, say that the information is unavailable.
Keep the answer concise, factual and professional.
Provide a confidence level at the end: High, Medium or Low.

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
    threshold = 0.50
    vector_store = _get_vector_store(state.get("collection", "default"))
    retrieved_docs_with_scores = vector_store.similarity_search_with_score(state["question"])
    retrieved_docs = [doc for doc, score in retrieved_docs_with_scores if score >= threshold]

    if not retrieved_docs:
        return {
            "context": [],
            "answer": "The information is unavailable in the current knowledge base.",
            "sources": [],
            "confidence_metric": 0.0,
        }

    top_scores = [score for _, score in retrieved_docs_with_scores if score >= threshold][:3]
    avg_score = round(sum(top_scores) / len(top_scores), 3)

    sources = [
        {"source": doc.metadata.get("source", "unknown"), "page": doc.metadata.get("page")}
        for doc in retrieved_docs
    ]
    return {"context": retrieved_docs, "answer": None, "sources": sources, "confidence_metric": avg_score}


async def generate(state: State):
    if state.get("answer"):
        return {"answer": state["answer"]}
    docs_content = "\n\n".join(doc.page_content for doc in state["context"])
    messages = prompt.invoke({"question": state["question"], "context": docs_content})
    llm = initialize_model_llm()
    full_response = ""
    async for chunk in llm.astream(messages):
        full_response += chunk.content
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
        vector_store.add_documents(documents=all_splits)
        cache_url(client, cache_key)
        print(f"Source '{source}' ingested into collection '{collection}'.")


async def stream_answer(question: str, collection: str = "default") -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events for a streaming chat response."""
    graph = _build_graph()
    sources = []

    async for event in graph.astream_events(
        {"question": question, "collection": collection},
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            content = event["data"]["chunk"].content
            if content:
                yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
        elif kind == "on_chain_end" and event.get("name") == "retrieve":
            output = event["data"].get("output", {})
            sources = output.get("sources", [])

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

import json
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

template = """Use the following pieces of context to answer the question at the end.
If you don't know the answer, just say that you don't know, don't try to make up an answer.
Use three sentences maximum and keep the answer as concise as possible.
Always say "thanks for asking!" at the end of the answer.

{context}

Question: {question}

Helpful Answer:"""
prompt = PromptTemplate.from_template(template)


class State(TypedDict):
    question: str
    context: List[Document]
    answer: str
    sources: List[dict]
    collection: str


def _get_vector_store(collection: str = "default"):
    config = PGVectorConfig(collection_name=collection)
    return create_vector_store(embeddings, store_type=VectorStoreType.PGVECTOR, config=config)


def retrieve(state: State):
    threshold = 0.50
    vector_store = _get_vector_store(state.get("collection", "default"))
    retrieved_docs_with_scores = vector_store.similarity_search_with_score(state["question"])
    retrieved_docs = [doc for doc, score in retrieved_docs_with_scores if score >= threshold]

    if not retrieved_docs:
        return {"context": [], "answer": "I don't know. thanks for asking!", "sources": []}

    sources = [
        {"source": doc.metadata.get("source", "unknown"), "page": doc.metadata.get("page")}
        for doc in retrieved_docs
    ]
    return {"context": retrieved_docs, "answer": None, "sources": sources}


def generate(state: State):
    if state.get("answer"):
        return {"answer": state["answer"]}
    docs_content = "\n\n".join(doc.page_content for doc in state["context"])
    messages = prompt.invoke({"question": state["question"], "context": docs_content})
    llm = initialize_model_llm()
    response = llm.invoke(messages)
    return {"answer": response.content}


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
    threshold = 0.50
    vector_store = _get_vector_store(collection)
    results = vector_store.similarity_search_with_score(question)
    docs = [doc for doc, score in results if score >= threshold]

    if not docs:
        yield f"data: {json.dumps({'type': 'token', 'content': \"I don't know. thanks for asking!\"})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    sources = [
        {"source": doc.metadata.get("source", "unknown"), "page": doc.metadata.get("page")}
        for doc in docs
    ]
    context = "\n\n".join(doc.page_content for doc in docs)
    messages = prompt.invoke({"question": question, "context": context})
    llm = initialize_model_llm()

    async for chunk in llm.astream(messages):
        if chunk.content:
            yield f"data: {json.dumps({'type': 'token', 'content': chunk.content})}\n\n"

    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def create_and_run_graph(question: str, collection: str = "default") -> dict:
    graph_builder = StateGraph(State).add_sequence([retrieve, generate])
    graph_builder.add_edge(START, "retrieve")
    graph = graph_builder.compile()
    response = graph.invoke({"question": question, "collection": collection})
    return {"answer": response["answer"], "sources": response.get("sources", [])}

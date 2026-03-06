import bs4
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import START, StateGraph
from langchain_core.prompts import PromptTemplate
from typing_extensions import List, TypedDict
from src.cache.factory import cache_url, get_redis_client, is_url_cached
from src.data_loading.pdf_loader import PDFDataLoader
from src.data_loading.text_splitter import split_text
from src.data_loading.webpage_loader import WebDataLoader
from src.utils.source_type import determine_source_type
from src.vector_store.embeddings import initialize_embeddings
from src.llm.llm_chain import initialize_model_llm
from src.vector_store.factory import create_vector_store, VectorStoreType


embeddings = initialize_embeddings()
vector_store = create_vector_store(embeddings, store_type=VectorStoreType.CHROMA)

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


def retrieve(state: State):
    threshold = 0.50
    retrieved_docs_with_scores = vector_store.similarity_search_with_score(state["question"])
    retrieved_docs = [
        doc for doc, score in retrieved_docs_with_scores if score >= threshold
    ]
    if not retrieved_docs:
        return {"context": [], "answer": "I don't know. thanks for asking!", "sources": []}

    sources = [
        {
            "source": doc.metadata.get("source", "unknown"),
            "page": doc.metadata.get("page"),
        }
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


def load_source(source):
    client = get_redis_client()

    if not is_url_cached(client, source):
        if determine_source_type(source) == "unknown":
            print(f"Source '{source}' is not a valid URL or PDF path.")
            return
        elif determine_source_type(source) == "pdf":
            loader = PDFDataLoader(source)
        else:
            loader = WebDataLoader(url=source)
        docs = loader.load()

        all_splits = split_text(docs)
        vector_store.add_documents(documents=all_splits)
        cache_url(client, source)
        print(f"Source '{source}' cached successfully.")


def create_and_run_graph(question) -> dict:
    graph_builder = StateGraph(State).add_sequence([retrieve, generate])
    graph_builder.add_edge(START, "retrieve")
    graph = graph_builder.compile()
    response = graph.invoke({"question": question})
    return {"answer": response["answer"], "sources": response.get("sources", [])}

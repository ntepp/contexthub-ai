import os

from langchain_ollama.embeddings import OllamaEmbeddings


def initialize_embeddings():
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return OllamaEmbeddings(model="nomic-embed-text", base_url=base_url)

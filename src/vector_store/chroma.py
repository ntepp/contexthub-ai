from typing import List
from langchain_chroma import Chroma
from langchain_core.documents import Document
from .base import BaseVectorStoreManager, VectorStoreConfig


class ChromaConfig(VectorStoreConfig):
    def __init__(self, collection_name="default_collection", persist_directory="./chroma_db", **kwargs):
        super().__init__(collection_name=collection_name, **kwargs)
        self.persist_directory = persist_directory


class ChromaVectorStoreManager(BaseVectorStoreManager):
    def __init__(self, embeddings, config: VectorStoreConfig = None):
        super().__init__(embeddings)
        self._config = config if isinstance(config, ChromaConfig) else ChromaConfig()

    def create_vector_store(self) -> Chroma:
        print("Using ChromaDB for vector storage")
        return Chroma(
            collection_name=self._config.collection_name,
            embedding_function=self._embeddings,
            persist_directory=self._config.persist_directory,
        )

    def document_exists(self, identifier: str) -> bool:
        results = self.create_vector_store().similarity_search(identifier, k=1)
        return len(results) > 0

    def add_documents(self, documents: List[Document]) -> None:
        self.create_vector_store().add_documents(documents)

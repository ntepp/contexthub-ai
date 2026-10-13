import os
from typing import List

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_postgres.vectorstores import PGVector

from .base import BaseVectorStoreManager, VectorStoreConfig

load_dotenv(override=True)


class PGVectorConfig(VectorStoreConfig):
    def __init__(self, collection_name: str = "default", **kwargs):
        super().__init__(collection_name=collection_name, **kwargs)
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        db = os.getenv("POSTGRES_DB", "contexthub")
        self.connection_string = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
        )


class PGVectorManager(BaseVectorStoreManager):
    def __init__(self, embeddings, config: VectorStoreConfig = None):
        super().__init__(embeddings)
        self._config = config if isinstance(config, PGVectorConfig) else PGVectorConfig()
        self._store: PGVector | None = None

    def create_vector_store(self) -> PGVector:
        # Memoised: each PGVector() opens its own connection pool, so rebuilding
        # it per call (e.g. once per ingestion batch) is pure overhead.
        if self._store is None:
            print(f"Using pgvector — collection: {self._config.collection_name}")
            self._store = PGVector(
                embeddings=self._embeddings,
                collection_name=self._config.collection_name,
                connection=self._config.connection_string,
                use_jsonb=True,
            )
        return self._store

    def document_exists(self, identifier: str) -> bool:
        results = self.create_vector_store().similarity_search(identifier, k=1)
        return len(results) > 0

    def add_documents(self, documents: List[Document]) -> None:
        self.create_vector_store().add_documents(documents)

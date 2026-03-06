from enum import Enum
from typing import Optional

from .base import BaseVectorStoreManager, VectorStoreConfig
from .in_memory import InMemoryVectorStoreManager
from .chroma import ChromaVectorStoreManager
from .pgvector import PGVectorManager


class VectorStoreType(Enum):
    IN_MEMORY = "in_memory"
    CHROMA = "chroma"
    PGVECTOR = "pgvector"


class VectorStoreFactory:
    _managers = {
        VectorStoreType.IN_MEMORY: InMemoryVectorStoreManager,
        VectorStoreType.CHROMA: ChromaVectorStoreManager,
        VectorStoreType.PGVECTOR: PGVectorManager,
    }

    @staticmethod
    def create_vector_store_manager(
        embeddings,
        store_type: VectorStoreType = VectorStoreType.PGVECTOR,
        config: Optional[VectorStoreConfig] = None,
    ) -> BaseVectorStoreManager:
        manager_class = VectorStoreFactory._managers.get(store_type)
        if not manager_class:
            raise ValueError(f"Unsupported vector store type: {store_type}")
        return manager_class(embeddings, config)


def create_vector_store(
    embeddings,
    store_type: VectorStoreType = VectorStoreType.PGVECTOR,
    config: Optional[VectorStoreConfig] = None,
):
    manager = VectorStoreFactory.create_vector_store_manager(embeddings, store_type, config)
    return manager.create_vector_store()

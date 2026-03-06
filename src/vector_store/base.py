from abc import ABC, abstractmethod
from typing import Any, List, Optional
from langchain_core.documents import Document

class BaseVectorStoreManager(ABC):
    """Abstract base class for vector store management."""
    
    def __init__(self, embeddings):
        """
        Initialize vector store manager.
        
        Args:
            embeddings: Embedding model to use for vectorization.
        """
        self._embeddings = embeddings
    
    @abstractmethod
    def create_vector_store(self) -> Any:
        """
        Create and return a vector store.
        
        Returns:
            Vector store instance.
        """
        pass
    
    @abstractmethod
    def document_exists(self, identifier: str) -> bool:
        """
        Check if a document exists in the vector store.
        
        Args:
            identifier (str): Unique identifier for the document.
        
        Returns:
            bool: True if document exists, False otherwise.
        """
        pass
    
    @abstractmethod
    def add_documents(self, documents: List[Document]) -> None:
        """
        Add documents to the vector store.
        
        Args:
            documents (List[Document]): Documents to add.
        """
        pass

class VectorStoreConfig:
    """Configuration class for vector store settings."""
    
    def __init__(
        self, 
        collection_name: str = "default_collection",
        vector_size: int = 768,
        distance_metric: str = "Cosine",
        url: Optional[str] = None
    ):
        """
        Initialize vector store configuration.
        
        Args:
            collection_name (str): Name of the vector store collection.
            vector_size (int): Dimensionality of vectors.
            distance_metric (str): Distance metric for vector comparison.
            url (Optional[str]): URL for remote vector store.
        """
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.distance_metric = distance_metric
        self.url = url
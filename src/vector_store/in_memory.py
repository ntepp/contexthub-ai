from typing import List
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_core.documents import Document

from .base import BaseVectorStoreManager

class InMemoryVectorStoreManager(BaseVectorStoreManager):
    """Manager for in-memory vector store."""
    
    def create_vector_store(self) -> InMemoryVectorStore:
        """
        Create an in-memory vector store.
        
        Returns:
            InMemoryVectorStore: Configured in-memory vector store.
        """
        print("Using InMemoryVectorStore")
        return InMemoryVectorStore(self._embeddings)
    
    def document_exists(self, identifier: str) -> bool:
        """
        Check if a document exists in the in-memory vector store.
        
        Args:
            identifier (str): URL or unique identifier.
        
        Returns:
            bool: True if document exists, False otherwise.
        """
        # Note: InMemoryVectorStore doesn't have a native way to check document existence
        # This is a placeholder implementation
        results = self.create_vector_store().similarity_search(identifier, k=1)
        return len(results) > 0
    
    def add_documents(self, documents: List[Document]) -> None:
        """
        Add documents to the in-memory vector store.
        
        Args:
            documents (List[Document]): Documents to add.
        """
        vector_store = self.create_vector_store()
        vector_store.add_documents(documents)
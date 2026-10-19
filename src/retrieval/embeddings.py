from functools import lru_cache

from src.config import get_settings

_embeddings_override = None


@lru_cache
def _build():
    settings = get_settings()
    if settings.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.ollama_base_url)
    if settings.embedding_provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embedding_model, api_key=settings.openai_api_key)
    if settings.embedding_provider == "openai_compatible":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embedding_model,
            base_url=settings.openai_compatible_base_url,
            api_key=settings.openai_compatible_api_key or "not-needed",
            check_embedding_ctx_length=False,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def get_embeddings():
    return _embeddings_override if _embeddings_override is not None else _build()


def set_embeddings_override(embeddings) -> None:
    global _embeddings_override
    _embeddings_override = embeddings

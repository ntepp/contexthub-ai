import os
from enum import Enum

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

load_dotenv(override=True)


class LLMProvider(Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"


class LLMFactory:
    @staticmethod
    def create(provider: LLMProvider = None, model_name: str = None):
        provider = provider or LLMProvider(os.getenv("LLM_PROVIDER", "ollama").lower())
        model_name = model_name or os.getenv("LLM_MODEL_NAME")

        if not model_name:
            raise ValueError(
                "No model name provided. Set LLM_MODEL_NAME in .env "
                "or pass model_name directly to initialize_model_llm()."
            )

        if provider == LLMProvider.OLLAMA:
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            return ChatOllama(model=model_name, base_url=base_url, temperature=0)

        if provider == LLMProvider.OPENAI:
            return ChatOpenAI(model=model_name, temperature=0)

        raise ValueError(f"Unsupported LLM provider: {provider}")

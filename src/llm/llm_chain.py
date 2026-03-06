from src.llm.factory import LLMFactory, LLMProvider


def initialize_model_llm(model_name=None, provider=None):
    llm_provider = LLMProvider(provider) if provider else None
    return LLMFactory.create(provider=llm_provider, model_name=model_name)

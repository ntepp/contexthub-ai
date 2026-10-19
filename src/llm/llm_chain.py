from src.llm.factory import LLMFactory, ModelSpec, resolve_model

# Tests replace this with a fake chat model; production code always goes through it.
_llm_override = None


def initialize_model_llm(spec: ModelSpec | None = None):
    if _llm_override is not None:
        return _llm_override
    return LLMFactory.create(spec or resolve_model(None))


def set_llm_override(llm) -> None:
    global _llm_override
    _llm_override = llm

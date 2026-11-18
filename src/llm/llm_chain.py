from src.llm.factory import LLMFactory, ModelSpec, resolve_model

# Tests replace this with a fake chat model; production code always goes through it.
_llm_override = None


def initialize_model_llm(spec: ModelSpec | None = None, *, user_id: str | None = None, step: str | None = None):
    """Chat model for one call. ``user_id`` (ContextHub UUID) and ``step`` (condense, plan,
    generate) attribute the call at the LLM gateway; ignored without a gateway."""
    if _llm_override is not None:
        return _llm_override
    return LLMFactory.create(spec or resolve_model(None), user_id=user_id, step=step)


def set_llm_override(llm) -> None:
    global _llm_override
    _llm_override = llm

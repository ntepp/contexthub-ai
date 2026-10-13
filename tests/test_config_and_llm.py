import pytest

from src.config import Settings
from src.llm.factory import LLMFactory, LLMProvider, ModelNotAllowed, parse_model_id, resolve_model


def test_production_refuses_insecure_defaults():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", secret_key="too-short", _env_file=None)
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        Settings(environment="production", secret_key="x" * 40, database_url=None, _env_file=None)
    ok = Settings(environment="production", secret_key="x" * 40, postgres_password="s3cret", _env_file=None)
    assert ok.secure_cookies is True


def test_tracing_is_off_by_default():
    assert Settings(_env_file=None).langchain_tracing_v2 is False


def test_redis_url_with_password():
    s = Settings(redis_password="pw", redis_host="redis", _env_file=None)
    assert s.redis_db_url(0) == "redis://:pw@redis:6379/0"


def test_model_ids_keep_colons_in_model_name():
    spec = parse_model_id("ollama:llama3.1:8b")
    assert spec.provider == LLMProvider.OLLAMA and spec.name == "llama3.1:8b"
    with pytest.raises(ValueError):
        parse_model_id("nope")
    with pytest.raises(ValueError):
        parse_model_id("acme:model")


def test_resolve_model_enforces_allow_list():
    assert resolve_model(None).id == "ollama:fake-model"
    with pytest.raises(ModelNotAllowed):
        resolve_model("openai:gpt-4o")


def test_factory_builds_each_provider(monkeypatch):
    from src.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "openai_compatible_base_url", "https://api.mistral.ai/v1")

    assert type(LLMFactory.create(parse_model_id("ollama:llama3.1:8b"))).__name__ == "ChatOllama"
    assert type(LLMFactory.create(parse_model_id("openai:gpt-4o"))).__name__ == "ChatOpenAI"
    assert type(LLMFactory.create(parse_model_id("anthropic:claude-sonnet-5-5"))).__name__ == "ChatAnthropic"
    compatible = LLMFactory.create(parse_model_id("openai_compatible:mistral-large-latest"))
    assert type(compatible).__name__ == "ChatOpenAI"
    assert "mistral" in str(compatible.openai_api_base)


def test_factory_reports_missing_keys(monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLMFactory.create(parse_model_id("anthropic:claude-sonnet-5-5"))

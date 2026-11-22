"""HF_HUB_OFFLINE: the re-ranker is read from the local cache only, with no call to the Hub."""

from __future__ import annotations

import os
import sys
import types

import pytest

from src.config import get_settings, reset_settings_cache
from src.retrieval import rerank
from tests.fakes import FakeTorch


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    calls: list[dict] = []

    class CrossEncoder:
        def __init__(self, model, **kwargs):
            calls.append({"model": model, **kwargs, "env": os.environ.get("HF_HUB_OFFLINE")})
            if kwargs.get("local_files_only") and model == "absent/model":
                raise OSError("not in cache")

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=CrossEncoder))
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    rerank._cross_encoder.cache_clear()
    yield calls
    rerank._cross_encoder.cache_clear()
    reset_settings_cache()


def test_offline_reads_the_local_cache_only(monkeypatch, fake_sentence_transformers):
    monkeypatch.setenv("HF_HUB_OFFLINE", "true")
    reset_settings_cache()
    assert get_settings().hf_hub_offline
    rerank._cross_encoder()
    assert fake_sentence_transformers[0]["local_files_only"] is True
    assert fake_sentence_transformers[0]["env"] == "1"


def test_online_by_default(monkeypatch, fake_sentence_transformers):
    reset_settings_cache()
    rerank._cross_encoder()
    assert fake_sentence_transformers[0]["local_files_only"] is False


def test_offline_without_the_model_explains_what_to_do(monkeypatch, fake_sentence_transformers):
    monkeypatch.setenv("HF_HUB_OFFLINE", "true")
    monkeypatch.setenv("RERANKER_MODEL", "absent/model")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="absent du cache local"):
        rerank._cross_encoder()

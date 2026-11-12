"""The model setting names the provider that shapes requests, and the server stays configurable."""

from __future__ import annotations

import pytest

from leitstand_backend.adapters.outbound.llm.agent_factory import (
    _SUPPORTED_PROVIDERS,
    _build_model,
)
from leitstand_backend.infrastructure.settings import Settings

_BASE_URL = "http://llm.example:8000/v1"


def _settings(model: str) -> Settings:
    return Settings(
        zenoh_disabled=True, auto_migrate=False, llm_model=model, llm_base_url=_BASE_URL
    )


@pytest.mark.parametrize(
    ("setting", "system", "model_name"),
    [
        (
            "openai-chat:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "openai",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ),
        ("vllm:qwen3.5-9b", "vllm", "qwen3.5-9b"),
        ("openrouter:google/gemini-2.5-flash", "openrouter", "google/gemini-2.5-flash"),
        ("ollama:llama3:8b", "ollama", "llama3:8b"),
    ],
)
def test_the_prefix_selects_the_provider_and_the_server_stays_the_configured_one(
    setting: str, system: str, model_name: str
) -> None:
    model = _build_model(_settings(setting))

    assert model.system == system
    assert model.model_name == model_name
    assert model.base_url.rstrip("/") == _BASE_URL
    assert model.client.max_retries == 0


@pytest.mark.parametrize(
    "setting",
    [
        pytest.param("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", id="no provider"),
        pytest.param("openai:gpt-4.1-mini", id="Responses API provider"),
        pytest.param("anthropic:claude-haiku-4-5", id="unsupported provider"),
        pytest.param("openrouter:", id="no model"),
        pytest.param("vllm: qwen3.5-9b", id="space before the model"),
    ],
)
def test_a_model_setting_without_a_supported_provider_is_refused(setting: str) -> None:
    with pytest.raises(ValueError, match="LEITSTAND_LLM_MODEL"):
        _build_model(_settings(setting))


@pytest.mark.parametrize("provider", _SUPPORTED_PROVIDERS)
def test_every_supported_provider_builds_on_our_own_client(provider: str) -> None:
    model = _build_model(_settings(f"{provider}:vendor/some-model"))

    assert model.base_url.rstrip("/") == _BASE_URL
    assert model.client.max_retries == 0

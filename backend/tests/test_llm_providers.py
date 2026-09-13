"""LLM provider behaviour, offline.

The guarantee under test is not "the model is clever" but "BlindSpot is never
worse off for having one configured": a failure degrades to deterministic
analysis, and the model can never influence the verdict.
"""
from __future__ import annotations

import httpx
import pytest

from app.config.settings import Settings
from app.providers.llm import NullLLMProvider, build_llm_provider
from app.providers.llm.http_providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    _describe,
    _parse_json,
)


def _settings(monkeypatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


class TestProviderSelection:
    def test_default_is_fully_local(self, monkeypatch):
        provider = build_llm_provider(_settings(monkeypatch, BLINDSPOT_LLM_PROVIDER="none"))
        assert isinstance(provider, NullLLMProvider)
        assert provider.available is False
        assert provider.is_external is False

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("anthropic", AnthropicProvider),
            ("openai", OpenAIProvider),
            ("gemini", GeminiProvider),
            ("google", GeminiProvider),
        ],
    )
    def test_named_providers_are_built(self, monkeypatch, name, expected):
        provider = build_llm_provider(
            _settings(monkeypatch, BLINDSPOT_LLM_PROVIDER=name, BLINDSPOT_LLM_API_KEY="k")
        )
        assert isinstance(provider, expected)

    def test_provider_without_key_falls_back_to_local(self, monkeypatch):
        monkeypatch.delenv("BLINDSPOT_LLM_API_KEY", raising=False)
        provider = build_llm_provider(_settings(monkeypatch, BLINDSPOT_LLM_PROVIDER="gemini"))
        assert isinstance(provider, NullLLMProvider)

    def test_unknown_provider_falls_back_to_local(self, monkeypatch):
        provider = build_llm_provider(
            _settings(monkeypatch, BLINDSPOT_LLM_PROVIDER="llama", BLINDSPOT_LLM_API_KEY="k")
        )
        assert isinstance(provider, NullLLMProvider)

    def test_stale_cross_provider_model_is_replaced(self, monkeypatch):
        """Switching provider without changing the model must not break every call."""
        provider = build_llm_provider(
            _settings(
                monkeypatch,
                BLINDSPOT_LLM_PROVIDER="gemini",
                BLINDSPOT_LLM_MODEL="claude-sonnet-5",
                BLINDSPOT_LLM_API_KEY="k",
            )
        )
        assert provider._model == "gemini-3.6-flash"

    def test_null_provider_reports_no_model(self, monkeypatch):
        provider = build_llm_provider(_settings(monkeypatch, BLINDSPOT_LLM_PROVIDER="none"))
        assert provider.model is None

    def test_provider_reports_the_resolved_model_not_the_configured_one(self, monkeypatch):
        """The UI must show what is really being called."""
        provider = build_llm_provider(
            _settings(
                monkeypatch,
                BLINDSPOT_LLM_PROVIDER="openrouter",
                BLINDSPOT_LLM_MODEL="claude-sonnet-5",
                BLINDSPOT_LLM_API_KEY="k",
            )
        )
        assert provider.model != "claude-sonnet-5"
        assert provider.model == provider._model

    def test_openrouter_targets_its_own_endpoint(self, monkeypatch):
        provider = build_llm_provider(
            _settings(monkeypatch, BLINDSPOT_LLM_PROVIDER="openrouter", BLINDSPOT_LLM_API_KEY="k")
        )
        assert isinstance(provider, OpenAIProvider)
        assert provider._base_url == "https://openrouter.ai/api/v1"
        assert provider._model.endswith(":free")
        assert provider._extra_headers.get("X-Title") == "BlindSpot"

    def test_base_url_override_enables_any_compatible_gateway(self, monkeypatch):
        """A local Ollama or vLLM server needs configuration, not new code."""
        provider = build_llm_provider(
            _settings(
                monkeypatch,
                BLINDSPOT_LLM_PROVIDER="openai",
                BLINDSPOT_LLM_API_KEY="k",
                BLINDSPOT_LLM_BASE_URL="http://localhost:11434/v1",
            )
        )
        assert provider._base_url == "http://localhost:11434/v1"

    def test_an_explicit_model_choice_is_respected(self, monkeypatch):
        provider = build_llm_provider(
            _settings(
                monkeypatch,
                BLINDSPOT_LLM_PROVIDER="gemini",
                BLINDSPOT_LLM_MODEL="gemini-flash-latest",
                BLINDSPOT_LLM_API_KEY="k",
            )
        )
        assert provider._model == "gemini-flash-latest"


class TestGeminiProvider:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider("test-key", "gemini-3.6-flash")

    def test_unconfigured_provider_never_calls_out(self, monkeypatch):
        def explode(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("a provider with no key must not make a request")

        monkeypatch.setattr(httpx, "post", explode)
        assert GeminiProvider(None, "gemini-3.6-flash").complete_json(system="s", prompt="p") is None

    def test_parses_a_normal_response(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": '{"feature": "Checkout"}'}]}}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        result = self._provider().complete_json(system="sys", prompt="hello")

        assert result == {"feature": "Checkout"}
        # The key belongs in a header, not a query string that lands in logs.
        assert captured["headers"]["x-goog-api-key"] == "test-key"
        assert "key=" not in str(captured["url"])
        assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "sys"

    def test_output_budget_has_a_floor(self, monkeypatch):
        """Reasoning tokens can consume the budget and truncate the JSON."""
        captured: dict[str, object] = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        self._provider().complete_json(system="s", prompt="p", max_tokens=100)

        budget = captured["json"]["generationConfig"]["maxOutputTokens"]
        assert budget >= GeminiProvider.MIN_OUTPUT_TOKENS

    def test_safety_block_with_no_candidates_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(
                200, json={"promptFeedback": {"blockReason": "SAFETY"}},
                request=httpx.Request("POST", url),
            ),
        )
        assert self._provider().complete_json(system="s", prompt="p") is None

    @pytest.mark.parametrize("status", [401, 404, 429, 500, 503])
    def test_http_errors_degrade_instead_of_raising(self, monkeypatch, status):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(status, text="nope", request=httpx.Request("POST", url)),
        )
        assert self._provider().complete_json(system="s", prompt="p") is None

    def test_network_failure_degrades_instead_of_raising(self, monkeypatch):
        def boom(url, **kwargs):
            raise httpx.ConnectError("unreachable", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", boom)
        assert self._provider().complete_json(system="s", prompt="p") is None

    def test_unparseable_body_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "I am not JSON"}]}}]},
                request=httpx.Request("POST", url),
            ),
        )
        assert self._provider().complete_json(system="s", prompt="p") is None


class TestOpenAICompatibleProvider:
    def _provider(self) -> OpenAIProvider:
        return OpenAIProvider(
            "k", "nex-agi/nex-n2.5-mini:free", base_url="https://openrouter.ai/api/v1"
        )

    def test_parses_a_normal_response(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": '{"feature": "Checkout"}'}}
                    ]
                },
                request=httpx.Request("POST", url),
            ),
        )
        assert self._provider().complete_json(system="s", prompt="p") == {"feature": "Checkout"}

    def test_gateway_error_body_on_a_200_is_handled(self, monkeypatch):
        """OpenRouter can return 200 carrying an upstream provider error."""
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(
                200,
                json={"error": {"code": 429, "message": "Provider returned error"}},
                request=httpx.Request("POST", url),
            ),
        )
        assert self._provider().complete_json(system="s", prompt="p") is None

    def test_output_budget_has_a_floor(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{}"}}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        self._provider().complete_json(system="s", prompt="p", max_tokens=50)
        assert captured["json"]["max_tokens"] >= OpenAIProvider.MIN_OUTPUT_TOKENS

    def test_null_content_does_not_raise(self, monkeypatch):
        """A reasoning model that produced only thoughts returns content: null."""
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: httpx.Response(
                200,
                json={"choices": [{"finish_reason": "length", "message": {"content": None}}]},
                request=httpx.Request("POST", url),
            ),
        )
        assert self._provider().complete_json(system="s", prompt="p") is None


class TestErrorDescriptions:
    @pytest.mark.parametrize(
        "status,fragment",
        [
            (401, "key was rejected"),
            (404, "BLINDSPOT_LLM_MODEL"),
            (429, "quota"),
            (503, "overloaded"),
        ],
    )
    def test_status_codes_get_an_actionable_hint(self, status, fragment):
        request = httpx.Request("POST", "https://example.test")
        error = httpx.HTTPStatusError(
            "x", request=request, response=httpx.Response(status, request=request)
        )
        message, code = _describe(error)
        assert code == status
        assert fragment in message

    def test_timeout_names_the_setting_to_change(self):
        message, code = _describe(httpx.TimeoutException("slow"))
        assert "BLINDSPOT_LLM_TIMEOUT_SECONDS" in message
        assert code is None


class TestJsonParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('{"a": 1}', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ('Here you go: {"a": 1}, hope that helps', {"a": 1}),
            ("[1, 2, 3]", None),
            ("", None),
            ("not json", None),
        ],
    )
    def test_tolerant_extraction(self, raw, expected):
        assert _parse_json(raw) == expected

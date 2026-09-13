"""HTTP-based LLM providers (Anthropic, OpenAI-compatible, Google Gemini).

All are optional. None is reachable unless the operator explicitly sets
`BLINDSPOT_LLM_PROVIDER` and supplies an API key.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ...config.logging_conf import get_logger
from .base import LLMProvider

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Tolerantly pull a JSON object out of a model response."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1 :] if "\n" in text else text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


#: Status codes an operator can actually act on, with the action.
_HTTP_HINTS = {
    401: "the API key was rejected",
    403: "the API key lacks access to this model",
    404: "the model name does not exist for this key, check BLINDSPOT_LLM_MODEL",
    429: "rate limit or quota exceeded, analysis continues without AI",
    500: "the provider had a server error",
    503: "the model is overloaded; retry later",
}


def _describe(exc: Exception) -> tuple[str, int | None]:
    """Turn a provider exception into an actionable message plus status code.

    Without this, a quota problem and a misconfigured model look identical in
    the log, both just 'call failed', and the operator has no idea whether to
    change a setting or wait.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        hint = _HTTP_HINTS.get(status, "unexpected status")
        return f"HTTP {status}: {hint}", status
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out, raise BLINDSPOT_LLM_TIMEOUT_SECONDS", None
    if isinstance(exc, httpx.RequestError):
        return f"network error: {exc}", None
    return f"{type(exc).__name__}: {exc}", None


class GeminiProvider(LLMProvider):
    """Google Gemini via the Generative Language API.

    Gemini has no dedicated system role, so the system prompt is sent as
    `systemInstruction`. `responseMimeType: application/json` asks for a raw
    JSON object, which is what every call site here expects.

    The key is sent in the `x-goog-api-key` header rather than the `?key=`
    query parameter: query strings end up in proxy logs and crash reports.
    """

    name = "gemini"

    #: Current Gemini models reason before answering. On some of them those
    #: thinking tokens are billed against `maxOutputTokens`, which truncates the
    #: JSON mid-object and makes the response unparseable. A floor well above
    #: what the answer itself needs keeps both model families working.
    #: `thinkingConfig.thinkingBudget = 0` is *not* used: several models reject
    #: it outright with HTTP 400.
    MIN_OUTPUT_TOKENS = 2048

    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete_json(
        self, *, system: str, prompt: str, max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            response = httpx.post(
                f"{self._base_url}/models/{self._model}:generateContent",
                headers={
                    "x-goog-api-key": self._api_key or "",
                    "Content-Type": "application/json",
                },
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max(max_tokens, self.MIN_OUTPUT_TOKENS),
                        "responseMimeType": "application/json",
                        # Deterministic wording keeps analyses reproducible.
                        "temperature": 0.0,
                    },
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()

            candidates = payload.get("candidates") or []
            if not candidates:
                # Usually a safety block; there is no text to parse.
                log.warning(
                    "llm returned no candidates, continuing deterministically",
                    extra={
                        "event": "llm.empty",
                        "provider": self.name,
                        "reason": str(payload.get("promptFeedback", ""))[:200],
                    },
                )
                return None

            candidate = candidates[0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                # Distinguish a truncated answer from a malformed one, so the
                # fix (raise the budget) is obvious from the log.
                log.warning(
                    "llm response truncated by the token budget",
                    extra={
                        "event": "llm.truncated",
                        "provider": self.name,
                        "model": self._model,
                    },
                )

            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(part.get("text", "") for part in parts)
            return _parse_json(text)
        except Exception as exc:
            reason, status = _describe(exc)
            log.warning(
                "llm call failed, continuing deterministically",
                extra={
                    "event": "llm.error",
                    "provider": self.name,
                    "model": self._model,
                    "status": status,
                    "error": reason,
                },
            )
            return None


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete_json(
        self, *, system: str, prompt: str, max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._api_key or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            blocks = payload.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return _parse_json(text)
        except Exception as exc:
            reason, status = _describe(exc)
            log.warning(
                "llm call failed, continuing deterministically",
                extra={
                    "event": "llm.error",
                    "provider": self.name,
                    "model": self._model,
                    "status": status,
                    "error": reason,
                },
            )
            return None


class OpenAIProvider(LLMProvider):
    """OpenAI and any gateway speaking the same chat-completions protocol.

    Pointing `base_url` elsewhere is enough to use OpenRouter, Together, vLLM,
    LM Studio or Ollama, which is the whole reason this is one class and not
    four.
    """

    name = "openai"

    #: Reasoning models spend part of `max_tokens` thinking before answering,
    #: which truncates the JSON. See GeminiProvider.MIN_OUTPUT_TOKENS.
    MIN_OUTPUT_TOKENS = 2048

    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        base_url: str = "https://api.openai.com/v1",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._extra_headers = extra_headers or {}

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete_json(
        self, *, system: str, prompt: str, max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    **self._extra_headers,
                },
                json={
                    "model": self._model,
                    "max_tokens": max(max_tokens, self.MIN_OUTPUT_TOKENS),
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()

            # A gateway can return a 200 carrying an upstream error body.
            if "error" in payload and "choices" not in payload:
                log.warning(
                    "llm gateway returned an error, continuing deterministically",
                    extra={
                        "event": "llm.error",
                        "provider": self.name,
                        "model": self._model,
                        "error": str(payload["error"])[:200],
                    },
                )
                return None

            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                log.warning(
                    "llm response truncated by the token budget",
                    extra={
                        "event": "llm.truncated",
                        "provider": self.name,
                        "model": self._model,
                    },
                )
            return _parse_json(choice["message"]["content"] or "")
        except Exception as exc:
            reason, status = _describe(exc)
            log.warning(
                "llm call failed, continuing deterministically",
                extra={
                    "event": "llm.error",
                    "provider": self.name,
                    "model": self._model,
                    "status": status,
                    "error": reason,
                },
            )
            return None

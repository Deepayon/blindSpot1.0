"""LLM provider registry."""
from __future__ import annotations

from ...config.logging_conf import get_logger
from ...config.settings import Settings, get_settings
from .base import LLMProvider, NullLLMProvider
from .http_providers import AnthropicProvider, GeminiProvider, OpenAIProvider

log = get_logger(__name__)

__all__ = [
    "LLMProvider",
    "NullLLMProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "build_llm_provider",
]

#: Sensible default model per provider, used when BLINDSPOT_LLM_MODEL still
#: holds another provider's default.
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    # Verified against the live API: gemini-2.5-flash and gemini-2.0-flash now
    # return 404 "no longer available to new users".
    "gemini": "gemini-3.6-flash",
    # A free-tier model that answers promptly with clean JSON (~5s). Several
    # other free models are reasoning-first: they spend the whole token budget
    # thinking, return an empty object, and can take minutes. OpenRouter's
    # free line-up changes often, see the note in .env.example.
    "openrouter": "nex-agi/nex-n2.5-mini:free",
}

#: Providers that speak the OpenAI chat-completions protocol, and their default
#: endpoint. Any other compatible gateway works via BLINDSPOT_LLM_BASE_URL.
_OPENAI_COMPATIBLE = {
    "openai": "https://api.openai.com/v1",
    "openai_compatible": "https://api.openai.com/v1",
    "azure_openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    name = settings.llm_provider

    if name in {"none", "", "null", "off", "disabled"}:
        return NullLLMProvider()

    model = _resolve_model(name, settings.llm_model)

    if name == "anthropic":
        provider: LLMProvider = AnthropicProvider(
            settings.llm_api_key, model, settings.llm_timeout_seconds
        )
    elif name in _OPENAI_COMPATIBLE:
        provider = OpenAIProvider(
            settings.llm_api_key,
            model,
            settings.llm_timeout_seconds,
            base_url=settings.llm_base_url or _OPENAI_COMPATIBLE[name],
            # Optional attribution headers; OpenRouter uses them for its
            # leaderboards and ignores them otherwise.
            extra_headers=(
                {"HTTP-Referer": "https://github.com/blindspot", "X-Title": "BlindSpot"}
                if name == "openrouter"
                else {}
            ),
        )
    elif name in {"gemini", "google"}:
        provider = GeminiProvider(settings.llm_api_key, model, settings.llm_timeout_seconds)
    else:
        log.warning(
            "unknown llm provider, external AI disabled",
            extra={"event": "llm.unknown", "provider": name},
        )
        return NullLLMProvider()

    if not provider.available:
        log.warning(
            "llm provider selected but no API key set; external AI disabled",
            extra={"event": "llm.unconfigured", "provider": name},
        )
        return NullLLMProvider()

    log.info(
        "external AI enabled",
        extra={"event": "llm.enabled", "provider": name, "model": model},
    )
    return provider


def _resolve_model(provider: str, configured: str) -> str:
    """Pick the model to use, tolerating a stale cross-provider default.

    Switching `BLINDSPOT_LLM_PROVIDER` without also changing
    `BLINDSPOT_LLM_MODEL` would otherwise send e.g. `claude-sonnet-5` to Gemini
    and fail every call. A model that belongs to another provider's defaults is
    replaced; anything the operator actually chose is left alone.
    """
    aliases = {"google": "gemini", "openai_compatible": "openai", "azure_openai": "openai"}
    canonical = aliases.get(provider, provider)
    default = _DEFAULT_MODELS.get(canonical)
    if default is None:
        return configured
    if not configured or configured in set(_DEFAULT_MODELS.values()) - {default}:
        return default
    return configured

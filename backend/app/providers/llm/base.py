"""LLMProvider abstraction.

The LLM is strictly an *advisor* in BlindSpot. It may enrich understanding and
wording; it may never write to the database, touch the filesystem, execute
anything, or overrule a deterministic fact. Every call site must work when the
provider returns `None`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def available(self) -> bool:
        """True when the provider is configured and usable."""

    @property
    def model(self) -> str | None:
        """The model actually in use.

        This can differ from `BLINDSPOT_LLM_MODEL`: switching provider without
        updating the model substitutes that provider's default. The UI must
        report what is really being called, not what was configured.
        """
        return getattr(self, "_model", None)

    @property
    def is_external(self) -> bool:
        """True when prompts leave the machine."""
        return True

    @abstractmethod
    def complete_json(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
    ) -> dict[str, Any] | None:
        """Return a parsed JSON object, or None if unavailable/unparseable.

        Implementations must never raise: an unreachable LLM degrades BlindSpot
        to deterministic-only analysis, it does not fail the request.
        """


class NullLLMProvider(LLMProvider):
    """The default. Keeps BlindSpot fully local and deterministic."""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    @property
    def is_external(self) -> bool:
        return False

    def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1024) -> None:
        return None

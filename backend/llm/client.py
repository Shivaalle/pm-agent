"""LLM client factory with provider fallback and no silent mock output."""

from __future__ import annotations

import time
from typing import List, Optional

from backend.config import settings
from backend.llm.providers.base import LLMProvider, LLMResponse, LLMUsage
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.providers.mock import MockProvider
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.logger import get_logger

logger = get_logger("llm.client")


class UnavailableProvider(LLMProvider):
    """Explicit provider used when configuration is incomplete."""

    def __init__(self, name: str, reason: str) -> None:
        self._name = name
        self._reason = reason

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return "unconfigured"

    @property
    def available(self) -> bool:
        return False

    async def generate_structured(self, system_instruction, user_prompt, response_schema, temperature=0.1):
        return LLMResponse(
            parsed=None,
            usage=LLMUsage(provider=self._name, model="unconfigured"),
            error=self._reason,
        )


class LLMClient:
    """Primary provider, optional backup, bounded retries, full usage trail."""

    def __init__(self, provider: Optional[LLMProvider] = None) -> None:
        self.provider = provider or self._build_provider()
        self._backup: Optional[LLMProvider] = None

    def _build_provider(self) -> LLMProvider:
        provider_name = (settings.AI_PROVIDER or "gemini").lower()

        if provider_name in {"gemini", "google"}:
            if not settings.GEMINI_API_KEY:
                return UnavailableProvider(
                    "gemini", "GEMINI_API_KEY is not set. Configure a provider or set AI_PROVIDER=mock."
                )
            return GeminiProvider(api_key=settings.GEMINI_API_KEY, model=settings.PRIMARY_MODEL)

        if provider_name in {"openai_compatible", "openai", "ollama", "selfhosted", "vllm"}:
            return OpenAICompatibleProvider(
                base_url=settings.OPENAI_BASE_URL,
                model=settings.OPENAI_MODEL,
                api_key=settings.OPENAI_API_KEY,
                name="openai_compatible",
            )

        if provider_name == "mock":
            return MockProvider()

        return UnavailableProvider(provider_name, f"Unknown AI_PROVIDER '{provider_name}'.")

    def set_backup(self, provider: LLMProvider) -> None:
        self._backup = provider

    @property
    def primary_name(self) -> str:
        return self.provider.name

    @property
    def primary_model(self) -> str:
        return self.provider.model

    async def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema,
        temperature: float = 0.1,
    ) -> LLMResponse:
        candidates: List[LLMProvider] = [self.provider]
        if self._backup is not None and self._backup.name != self.provider.name:
            candidates.append(self._backup)
        if settings.AI_ALLOW_MOCK_FALLBACK and all(c.name != "mock" for c in candidates):
            candidates.append(MockProvider())

        attempts: List[LLMUsage] = []
        errors: List[str] = []
        started = time.perf_counter()
        for provider in candidates:
            if not provider.available:
                result = await provider.generate_structured(
                    system_instruction, user_prompt, response_schema, temperature
                )
                attempts.extend(result.attempts)
                errors.append(f"{provider.name}: {result.error}")
                continue
            for retry in range(settings.AI_MAX_RETRIES + 1):
                result = await provider.generate_structured(
                    system_instruction,
                    user_prompt,
                    response_schema,
                    max(0.0, temperature - retry * 0.05),
                )
                attempts.extend(result.attempts)
                if result.ok:
                    result.attempts = attempts
                    logger.info(
                        "Structured generation succeeded via %s/%s in %.0f ms",
                        provider.name,
                        provider.model,
                        (time.perf_counter() - started) * 1000,
                    )
                    return result
                errors.append(f"{provider.name}: {result.error}")
                logger.warning(
                    "Provider %s attempt %d failed: %s", provider.name, retry + 1, result.error
                )

        return LLMResponse(
            parsed=None,
            usage=attempts[-1] if attempts else None,
            error="all AI providers failed: " + " | ".join(errors[-4:]),
            attempts=attempts,
        )


_client_singleton: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = LLMClient()
    return _client_singleton

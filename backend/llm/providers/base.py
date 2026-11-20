"""Provider interface for structured meeting extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from backend.config import settings


class LLMUsage:
    """Token usage and estimated cost for a single provider attempt."""

    def __init__(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        self.estimated_cost_usd = estimated_cost_usd

    def model_dump(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = settings.model_price(model)
    return (
        prompt_tokens / 1_000_000 * price["input"]
        + completion_tokens / 1_000_000 * price["output"]
    )


class LLMResponse:
    """Result of a structured generation, including every attempted call."""

    def __init__(
        self,
        parsed: Optional[BaseModel],
        usage: Optional[LLMUsage],
        raw: Optional[str] = None,
        error: Optional[str] = None,
        attempts: Optional[List[LLMUsage]] = None,
    ) -> None:
        self.parsed = parsed
        self.usage = usage
        self.raw = raw
        self.error = error
        self.attempts = attempts or ([usage] if usage else [])

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None


class LLMProvider(ABC):
    """Protocol every AI provider must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        ...

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    async def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        temperature: float = 0.1,
    ) -> LLMResponse:
        ...

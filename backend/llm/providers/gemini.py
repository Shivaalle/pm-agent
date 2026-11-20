"""Google Gemini provider using the official google-genai SDK."""

from __future__ import annotations

import json
from typing import Any, Type

from google import genai
from google.genai import types
from pydantic import BaseModel

from backend.llm.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    estimate_cost,
)
from backend.logger import get_logger

logger = get_logger("llm.gemini")


class GeminiProvider(LLMProvider):
    """Gemini with strict Pydantic response_schema output."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        temperature: float = 0.1,
    ) -> LLMResponse:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=user_prompt)],
                    )
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=temperature,
                ),
            )
        except Exception as exc:
            logger.warning("Gemini generation failed: %s", exc)
            return LLMResponse(
                parsed=None,
                usage=LLMUsage(provider="gemini", model=self._model),
                error=str(exc),
            )

        usage = self._extract_usage(response)
        raw = getattr(response, "text", None) or ""
        if not raw:
            return LLMResponse(
                parsed=None,
                usage=usage,
                error="Gemini returned an empty response (possibly blocked)",
            )
        try:
            parsed = response_schema.model_validate(json.loads(raw))
        except Exception as exc:
            logger.warning("Gemini output failed schema validation: %s", exc)
            return LLMResponse(
                parsed=None,
                usage=usage,
                raw=raw,
                error=f"schema validation failed: {exc}",
            )
        return LLMResponse(parsed=parsed, usage=usage, raw=raw)

    def _extract_usage(self, response: Any) -> LLMUsage:
        meta = getattr(response, "usage_metadata", None)
        prompt_tokens = 0
        completion_tokens = 0
        if meta is not None:
            prompt_tokens = int(
                getattr(meta, "prompt_token_count", None)
                or (meta.get("prompt_token_count", 0) if isinstance(meta, dict) else 0)
                or 0
            )
            completion_tokens = int(
                getattr(meta, "candidates_token_count", None)
                or (meta.get("candidates_token_count", 0) if isinstance(meta, dict) else 0)
                or 0
            )
        return LLMUsage(
            provider="gemini",
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=estimate_cost(self._model, prompt_tokens, completion_tokens),
        )

"""OpenAI-compatible provider for self-hosted or private AI deployments.

Works with Ollama, vLLM, LM Studio, LocalAI, Azure OpenAI-compatible gateways
and hosted OpenAI-compatible APIs. This is the escape hatch for companies that
cannot send data to a public AI service (PRD: replaceable AI provider).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Type

import httpx
from pydantic import BaseModel

from backend.llm.providers.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    estimate_cost,
)
from backend.logger import get_logger

logger = get_logger("llm.openai_compatible")


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        name: str = "openai_compatible",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._name = name
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def generate_structured(
        self,
        system_instruction: str,
        user_prompt: str,
        response_schema: Type[BaseModel],
        temperature: float = 0.1,
    ) -> LLMResponse:
        schema = response_schema.model_json_schema()
        system = (
            f"{system_instruction}\n\n"
            "Return a single JSON object only. It must validate against this JSON Schema:\n"
            f"{json.dumps(schema)}"
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", headers=headers, json=payload
                )
                # Many local servers only implement json_object mode. Retry once
                # with that mode rather than failing the meeting.
                if response.status_code in {400, 422}:
                    payload["response_format"] = {"type": "json_object"}
                    payload["messages"][0]["content"] = (
                        f"{system}\nReturn only the JSON object."
                    )
                    response = await client.post(
                        f"{self._base_url}/chat/completions", headers=headers, json=payload
                    )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("%s generation failed: %s", self._name, exc)
            return LLMResponse(
                parsed=None,
                usage=LLMUsage(provider=self._name, model=self._model),
                error=str(exc),
            )

        usage_data = data.get("usage", {}) or {}
        prompt_tokens = int(usage_data.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_data.get("completion_tokens", 0) or 0)
        usage = LLMUsage(
            provider=self._name,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage_data.get("total_tokens", 0) or prompt_tokens + completion_tokens),
            estimated_cost_usd=estimate_cost(self._model, prompt_tokens, completion_tokens),
        )
        try:
            raw = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            return LLMResponse(parsed=None, usage=usage, error=f"invalid response: {exc}")
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        try:
            parsed = response_schema.model_validate(json.loads(raw))
        except Exception as exc:
            logger.warning("%s output failed schema validation: %s", self._name, exc)
            return LLMResponse(
                parsed=None, usage=usage, raw=raw, error=f"schema validation failed: {exc}"
            )
        return LLMResponse(parsed=parsed, usage=usage, raw=raw)

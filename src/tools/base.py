"""
LLM client wrapper — the single interface all agents use for LLM calls.

Design decision: wrap the OpenAI client in a thin layer that:
1. Centralizes model selection (sub-step vs. synthesis model from config)
2. Tracks token usage for cost analysis (critical for Track A's cost-quality curve)
3. Supports JSON mode for structured outputs (claims, verification results)
4. Falls back gracefully on rate limits / errors

Why not call openai directly everywhere? Centralized token tracking is required
for the adaptive compute cost-quality analysis. Also makes ablation across
models trivial — just change the config.

Free-model note (deepseek-r1:free etc.): many free OpenRouter models do NOT
support response_format={"type": "json_object"}. complete_json therefore tries
JSON mode first and falls back to prompt-based JSON extraction from plain text.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI


@dataclass
class LLMResponse:
    """The result of an LLM call, with usage tracking."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _extract_json_from_text(text: str) -> dict:
    """
    Best-effort extraction of a JSON object from a free-form LLM response.
    Handles ```json fenced blocks and bare {...} objects.
    """
    # Try fenced ```json ... ``` first
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Try the first balanced-looking {...} block
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    return {"raw_text": text}


class LLMClient:
    """
    Thin wrapper around the OpenAI API.

    Uses environment variable OPENAI_API_KEY by default.
    Supports OpenRouter via base_url override.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ):
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> LLMResponse:
        """Standard chat completion with token tracking."""
        start = time.time()
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        latency = (time.time() - start) * 1000
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return LLMResponse(
            text=text,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency,
        )

    def complete_json(self, system: str, user: str) -> tuple[dict, LLMResponse]:
        """
        Chat completion with JSON response mode.
        Returns (parsed_json, llm_response).

        Tries response_format json_object first. If the model rejects it
        (common with free/reasoning models on OpenRouter), retries without
        response_format and extracts JSON from the text via regex.
        """
        start = time.time()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Model does not support response_format — retry plain text
            # and extract JSON from the output.
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system + "\n\nReturn ONLY a valid JSON object, no prose."},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        latency = (time.time() - start) * 1000
        text = resp.choices[0].message.content or "{}"
        usage = resp.usage
        llm_resp = LLMResponse(
            text=text,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency,
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _extract_json_from_text(text)
        return parsed, llm_resp

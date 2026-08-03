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
import random
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


def _extract_message_text(resp, default: str) -> str:
    """
    Pull the completion text out of an API response defensively.

    `resp.choices[0].message.content` assumes a well-formed response, but a
    provider can return HTTP 200 with `choices: null` — observed with
    deepseek-chat via OpenRouter during real-model testing (a soft failure
    that doesn't raise an exception the SDK would catch). This is a system
    boundary: treat a missing/empty response the same as an empty completion
    rather than crashing the whole pipeline on one bad call.
    """
    choices = getattr(resp, "choices", None)
    if not choices:
        return default
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message else None
    return content or default


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


# Errors worth retrying: transient server-side or throttling conditions. A 400
# or 404 will fail identically no matter how long you wait, so retrying those
# just burns the budget slower.
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    if status in _RETRYABLE_STATUS:
        return True
    # Connection-level failures carry no status but are transient by nature.
    name = type(exc).__name__
    return name in {"APIConnectionError", "APITimeoutError", "InternalServerError",
                    "RateLimitError", "ConnectionError", "Timeout"}


class LLMClient:
    """
    Thin wrapper around the OpenAI API.

    Uses environment variable OPENAI_API_KEY by default.
    Supports OpenRouter via base_url override.

    RETRIES. Rate limits (429) crashed two multi-hour evaluation runs outright
    before this existed, and running sub-questions in parallel multiplies the
    request rate by the worker count -- so backoff is a precondition for
    concurrency, not a nicety. Retries use exponential backoff with jitter;
    the jitter matters because N parallel workers that all fail at once would
    otherwise retry in lockstep and reproduce the same burst that throttled
    them.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        max_retries: int = 4,
        backoff_base: float = 1.5,
    ):
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def _with_retry(self, fn):
        """
        Call `fn`, retrying transient failures with jittered exponential backoff.

        Returns the last exception rather than raising when retries are
        exhausted on a retryable error, so one throttled sub-question degrades
        to an empty result instead of killing a whole run.
        """
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                return fn()
            except Exception as exc:
                last = exc
                if attempt >= self.max_retries or not _is_retryable(exc):
                    raise
                delay = (self.backoff_base ** attempt) + random.uniform(0, 0.75)
                time.sleep(delay)
        raise last

    def complete(self, system: str, user: str) -> LLMResponse:
        """Standard chat completion with token tracking."""
        start = time.time()
        resp = self._with_retry(lambda: self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        ))
        latency = (time.time() - start) * 1000
        text = _extract_message_text(resp, default="")
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
            resp = self._with_retry(lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            ))
        except Exception:
            # Model does not support response_format — fall back to plain text
            # and extract JSON from the output. Note this catch is deliberately
            # broad: it also absorbs a retry-exhausted transient failure, which
            # then gets one more (retried) attempt in the simpler shape.
            resp = self._with_retry(lambda: self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system + "\n\nReturn ONLY a valid JSON object, no prose."},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            ))
        latency = (time.time() - start) * 1000
        text = _extract_message_text(resp, default="{}")
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

        # Every caller's system prompt asks for a JSON OBJECT, but valid JSON
        # includes bare lists, numbers, and strings too — a model can return
        # `["some claim"]` instead of `{"claims": ["some claim"]}` and still
        # have technically produced valid JSON. Observed in practice with
        # gemma-4-26b via OpenRouter during the 2026-08 evaluation, where it
        # crashed verify_claims (`'list' object has no attribute 'get'`).
        # Every caller does result.get(...) assuming a dict, so enforce that
        # guarantee once here rather than defensively in every agent.
        if not isinstance(parsed, dict):
            parsed = {"_unparsed": parsed}
        return parsed, llm_resp

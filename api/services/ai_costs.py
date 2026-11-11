"""Small AI usage/cost helpers for provider metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AIUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    thinking_tokens: int = 0
    # Part of prompt_tokens served from the provider's prompt cache.
    cached_prompt_tokens: int = 0


@dataclass(frozen=True)
class AIModelPricing:
    input_per_million_usd: float
    output_per_million_usd: float
    # None: cached input is billed like regular input (no published rate).
    cached_input_per_million_usd: float | None = None


_MODEL_PRICING: dict[str, AIModelPricing] = {
    # Google Gemini API, paid tier standard pricing as of 2026-05.
    "gemini-2.5-flash": AIModelPricing(0.30, 2.50),
    "gemini-2.5-flash-lite": AIModelPricing(0.10, 0.40),
    "gemini-3-flash-preview": AIModelPricing(0.50, 3.00),
    # OpenAI current low-cost references. Used only when deployments
    # switch AI_BASE_URL/AI_MODEL; unknown models simply report $0.
    "gpt-5.4-mini": AIModelPricing(0.75, 4.50),
    "gpt-5.4-nano": AIModelPricing(0.20, 1.25),
    # AI-REVIEW-3.9: OpenAI standard tier, <= 272K input, as of 2026-11.
    "gpt-6-luna": AIModelPricing(0.10, 0.50, cached_input_per_million_usd=0.01),
}


def _int_from_usage(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def extract_ai_usage(payload: dict[str, Any]) -> AIUsage:
    usage = (
        payload.get("usage")
        or payload.get("usage_metadata")
        or payload.get("usageMetadata")
        or {}
    )
    if not isinstance(usage, dict):
        return AIUsage()

    completion_details = usage.get("completion_tokens_details") or {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}

    prompt_tokens = _int_from_usage(
        usage, "prompt_tokens", "prompt_token_count", "promptTokenCount"
    )
    completion_tokens = _int_from_usage(
        usage, "completion_tokens", "candidates_token_count", "candidatesTokenCount"
    )
    total_tokens = _int_from_usage(usage, "total_tokens", "total_token_count", "totalTokenCount")
    thinking_tokens = _int_from_usage(
        usage, "thoughts_token_count", "thoughtsTokenCount"
    ) or _int_from_usage(completion_details, "reasoning_tokens")

    cached_prompt_tokens = _int_from_usage(prompt_details, "cached_tokens") or _int_from_usage(
        usage, "cached_content_token_count", "cachedContentTokenCount"
    )

    return AIUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        thinking_tokens=thinking_tokens,
        cached_prompt_tokens=min(cached_prompt_tokens, prompt_tokens),
    )


def estimate_ai_cost_usd(model: str, usage: AIUsage) -> float:
    pricing = _MODEL_PRICING.get(_normalise_model_key(model))
    if pricing is None:
        return 0.0

    billable_output = usage.completion_tokens
    if usage.total_tokens and usage.prompt_tokens:
        billable_output = max(billable_output, usage.total_tokens - usage.prompt_tokens)
    elif usage.thinking_tokens:
        billable_output += usage.thinking_tokens

    cached = usage.cached_prompt_tokens
    cached_rate = pricing.cached_input_per_million_usd
    if cached_rate is None:
        cached_rate = pricing.input_per_million_usd
    return (
        (usage.prompt_tokens - cached) * pricing.input_per_million_usd
        + cached * cached_rate
        + billable_output * pricing.output_per_million_usd
    ) / 1_000_000


def _normalise_model_key(model: str) -> str:
    key = (model or "").strip().lower()
    if "/" in key:
        key = key.rsplit("/", 1)[-1]
    return key

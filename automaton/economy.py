"""Compute pricing and survival tiers."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Tier

# USD per million tokens: (input, output, cache_read, cache_write)
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
}
FALLBACK_PRICE = PRICES["claude-opus-5-5"]  # price unknown models conservatively


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


def llm_cost(model: str, usage: Usage) -> float:
    pin, pout, pread, pwrite = PRICES.get(model, FALLBACK_PRICE)
    return (
        usage.input_tokens * pin
        + usage.output_tokens * pout
        + usage.cache_read_input_tokens * pread
        + usage.cache_creation_input_tokens * pwrite
    ) / 1_000_000


def host_cost(seconds: float, per_hour_usd: float) -> float:
    return seconds / 3600 * per_hour_usd


def tier_for(balance_usd: float, tiers: list[Tier]) -> Tier | None:
    """The richest tier the balance qualifies for; None means the automaton is dead."""
    if balance_usd <= 0:
        return None
    for tier in sorted(tiers, key=lambda t: -t.min_balance_usd):
        if balance_usd >= tier.min_balance_usd:
            return tier
    return None

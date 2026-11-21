"""Model usage metering (T-099, ADR 0017): what each scenario run cost, per tier.

    with usage.collect() as used:          # around a scenario call (the pipeline does this)
        outcome = await generate_template(url)
    used  # [ModelUsage(scenario="attach", tier="strong", model=..., requests=..., cost_usd=...), ...]

`run_scenario` calls `record()` once per tier that made model calls. Without a collector (CLIs, tests) it's a no-op.
The agent never writes the database: the caller stores what was collected (`core.store.add_model_usage`).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

# USD per 1M tokens: (input, cached input, output). OpenAI list prices; `WATCHTOWER_MODEL_PRICES` adds or overrides.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}
PRICES_ENV = "WATCHTOWER_MODEL_PRICES"


@dataclass(frozen=True)
class ModelUsage:
    scenario: str  # "attach" | "investigate"
    tier: str  # "strong" | "light"
    model: str
    requests: int
    input_tokens: int  # cached tokens included
    cached_tokens: int
    output_tokens: int
    cost_usd: float | None  # None: no price known for the model

    def as_row(self) -> dict:
        return asdict(self)


def prices() -> dict[str, tuple[float, float, float]]:
    table = dict(PRICES)
    raw = os.environ.get(PRICES_ENV, "").strip()
    if raw:
        try:
            for model, p in json.loads(raw).items():
                inp, cached, out = (float(x) for x in p)
                table[model] = (inp, cached, out)
        except (ValueError, TypeError, AttributeError) as e:
            log.warning('ignoring %s: expected {"model": [input, cached, output]} per 1M tokens (%s)', PRICES_ENV, e)
    return table


def price_for(model: str) -> tuple[float, float, float] | None:
    return prices().get(model)


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    p = price_for(model)
    if p is None:
        return None
    inp, cached, out = p
    uncached = max(0, input_tokens - cached_tokens)
    return (uncached * inp + cached_tokens * cached + output_tokens * out) / 1_000_000


_collector: ContextVar[list[ModelUsage] | None] = ContextVar("watchtower_model_usage", default=None)


@contextmanager
def collect() -> Iterator[list[ModelUsage]]:
    """Gather the usage of every scenario run started inside the block (in this task and the tasks it awaits)."""
    used: list[ModelUsage] = []
    token = _collector.set(used)
    try:
        yield used
    finally:
        _collector.reset(token)


def record(
    scenario: str, tier: str, model: str, *, requests: int, input_tokens: int, cached_tokens: int, output_tokens: int
) -> ModelUsage | None:
    """Report one scenario run's usage for one tier. Returns the record (None when no model call was made)."""
    if requests <= 0:
        return None
    u = ModelUsage(
        scenario=scenario,
        tier=tier,
        model=model,
        requests=requests,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd(model, input_tokens, cached_tokens, output_tokens),
    )
    if (used := _collector.get()) is not None:
        used.append(u)
    return u

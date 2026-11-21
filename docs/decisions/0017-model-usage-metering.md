# 0017 — Model usage metering

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-099

## Context
The brief asks for the model cost of one portal, and the dashboard should show what the agent cost. Every model call
already goes through `agent.runtime` (`run_agent`, ADR 0010), tagged with its tier (ADR 0015). `run_scenario` returns
usage per tier, but the scenario callables the pipeline uses (`GenerateTemplate`, `Investigate`, ADR 0009) return only
an outcome, and `agent` must never touch the database (ADR 0002).

## Decision
- **Collector, no interface change.** `watchtower.agent.usage` holds a `ContextVar`. `with usage.collect() as used:`
  around a scenario call gathers one `ModelUsage` per scenario run and tier that made calls (`scenario`, `tier`,
  `model`, `requests`, `input_tokens`, `cached_tokens`, `output_tokens`, `cost_usd`). `run_scenario` reports to it.
  Without a collector (CLIs, tests), recording is a no-op. `GenerateTemplate`/`Investigate` keep their signatures.
- **Pricing.** `usage.price_for(model)` → USD per 1M input, cached-input and output tokens. Built-in table: the
  default model only (`gpt-5.6-luna`: 0.20 / 0.02 / 1.20, OpenAI list price). `WATCHTOWER_MODEL_PRICES` (JSON,
  `{"model": [input, cached, output]}`) adds or overrides models. An unknown model's `cost_usd` is `null`, never a guess.
  Cached tokens are billed at the cached rate, the rest of the input at the input rate; the long-prompt surcharge
  (> 272K input tokens in one request) is ignored because our prompts stay far below it.
- **Storage.** Migration 2 adds `model_usage` (one row per run, scenario and tier: `run_id`, `portal_id`, the fields
  above, `created_at`). The pipeline writes the collected rows with `store.add_model_usage(portal_id, run_id, rows)`
  right after the scenario returns, whatever the outcome. `store.model_usage_summary()` sums them per portal and
  scenario (`attach`, `investigate`).
- **Shown** on the dashboard (overview: model cost per portal; portal page: calls, tokens and cost per scenario and
  tier). The HTTP API contract is unchanged.

## Consequences
- New model call sites must go through `run_scenario` (or report to `usage.record`) to be metered.
- Prices change: the built-in table must be kept current, or `WATCHTOWER_MODEL_PRICES` set. A cost shown as unknown
  means a model without a price.
- Regular reads make no model call, so they never add rows; a test asserts it.

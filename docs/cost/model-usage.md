# Model usage & cost per portal

Regular reads make **no model call**: the engine runs the stored template. The model runs only to **attach** a portal,
to **investigate** a failed read (repair), and in proactive mode for an **audit** that found something suspicious. An
audit with no findings ends without a model call ([investigate](../repair/investigate.md)).

## How it is metered

[ADR 0017](../decisions/0017-model-usage-metering.md), code in `watchtower/agent/usage.py`:

- Every model call goes through `agent.runtime` (`run_agent`, tagged with its tier). `run_scenario` reports the usage
  of each tier that made calls, and the pipeline stores it per run in the `model_usage` table, whatever the outcome.
- **Cost** = uncached input × input price + cached input × cached price + output × output price. Built-in price:
  `gpt-5.6-luna` at **$0.20 / $0.02 / $1.20** per 1M input / cached-input / output tokens (OpenAI list price). Other
  models: `WATCHTOWER_MODEL_PRICES='{"model": [input, cached, output]}'`. A model without a price shows
  "price unknown", never a guessed amount.
- **Dashboard:** the overview shows each portal's model cost and the total; the portal page splits calls, tokens and
  cost by scenario, tier and model.

## Measured: one attach

Live runs on the [mock portals](../test-portals/mock-portals.md) with `gpt-5.6-luna` for both tiers
(`python -m watchtower.agent.probe --mock KIND`), 2026-11-21:

| Portal | Outcome | Model calls (strong + light) | Input tokens (cached) | Output tokens | Time | Cost |
|---|---|---|---|---|---|---|
| `ssr` (table + detail pages) | `TemplateFound` | 7 + 2 | 57,144 (45,387) | 2,807 | 53 s | **$0.0066** |
| `js` (JSON API) | `TemplateFound` | 6 + 0 | 43,326 (34,105) | 1,692 | 39 s | **$0.0046** |
| `form` (search form) | `TemplateFound` | 7 + 0 | 49,911 (40,938) | 1,564 | 36 s | **$0.0045** |
| `login` (documents behind a login) | `Blocked`, after checking two tenders | 6 + 1 | 41,716 (33,379) | 578 | 27 s | **$0.0030** |

About 80 % of the input is served from the provider's prompt cache (the instructions are static), so input costs a
tenth of its list price. Most of the cost is output tokens.

## Cost of one portal

- **Attach:** about **$0.005** on these portals. The budget caps an attach at 40 strong turns / 600k tokens plus
  60 light calls / 400k tokens; at the token mix measured above, a run that uses the whole budget costs about **$0.11**.
- **Regular reads:** **$0**, however often they run.
- **Repair** (only when a read fails): capped at 25 strong turns / 300k tokens plus 30 light calls / 200k tokens, about
  **$0.06** at most at the same mix.
- **Audits** (proactive mode, every 24 h by default): $0 when the deterministic checks find nothing; otherwise at most
  15 strong turns / 150k tokens plus light calls.

So a portal that attaches cleanly and stays stable costs **about half a US cent, once**. Real portals take more
exploration than the mocks; the [v2 live re-run](../portals/registry.md#smoke-runs) (T-038) measures them, and the
dashboard shows the real figure for every portal as it runs.

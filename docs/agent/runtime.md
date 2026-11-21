# Agent runtime and tools

`watchtower.agent` is the **only package that calls a model** ([ADR 0002](../decisions/0002-architecture.md), [ADR 0010](../decisions/0010-agent-runtime-and-tools.md)). It
learns how to read a portal and writes a [template](../decisions/0003-template-format.md); it never writes code and never
touches the database or portal state. Scenarios (`generate_template`, T-015; `investigate`, T-016) are built on the
runtime described here.

## Principle: the model judges, tools compute

Every tool returns a compact text summary, never a raw page. The model decides which block is the tender list and which
value is which field; tools fetch, prune, dry-run and check. Anything that can be computed deterministically goes into
a tool (T-022 adds more), not into the prompt.

## Running a scenario

```python
from watchtower.agent.context import AgentContext
from watchtower.agent.runtime import Budget, TieredBudget, run_scenario

ctx = AgentContext(portal_url, client, browser)            # PoliteClient / BrowserPool from net
budget = TieredBudget(strong=Budget(max_turns=40), light=Budget(max_turns=60))
run = await run_scenario("attach", SCENARIO_PROMPT, f"Portal URL: {portal_url}", ctx, budget=budget)
run.outcome      # an ADR 0009 outcome (TemplateFound, Blocked, Unreadable, Transient, …) or None
run.stop_reason  # outcome | turns | tokens | time | no_outcome
run.requests, run.input_tokens, run.cached_tokens, run.output_tokens, run.seconds   # both tiers summed
run.by_tier["strong"], run.by_tier["light"]              # TierUsage: requests, input/cached/output tokens
```

- **Model and key:** `OPENAI_API_KEY` (required) and one model per [tier](#model-tiers) from the environment
  (`config.py`). `run_agent(..., tier=)` in `runtime.py` is the one function every model call goes through, tagged
  with its tier (`workflow_name` `watchtower:strong` / `watchtower:light`), so T-099 can meter usage there per tier.
  Reasoning effort defaults to `low`.
- **The run ends when a tool records an outcome** (`submit_template` accepted, or a `report_*` tool). A refused
  `submit_template` returns its reasons and the model continues.
- **Budget** (`TieredBudget(strong=Budget(...), light=Budget(...))`; a plain `Budget` is the strong tier with the
  default light budget): for the strong tier, turns are model calls, tokens are summed over its calls, and its
  `wall_seconds` is the whole run's wall time, tools and light calls included. Exceeding one stops the run with
  `outcome=None` and the `stop_reason`; it never raises. The light tier's limits are calls (`max_turns`), tokens, and
  `wall_seconds` per single call; when they run out, `delegate_*` tools answer "do this check yourself" and the
  strong model goes on.
- **Nudges:** a model that answers with plain text instead of a closing tool call is reminded, at most twice, within
  the same budget.
- **Instructions** (`prompts.py`): rules (a wrong value is worse than a missing one; no login or captcha solving; try
  first, then decide), the working method, a compact template reference and two example templates. The text is static,
  so the provider's prompt cache serves it cheaply after the first call. Scenario prompts are appended to it.

## Model tiers

[ADR 0015](../decisions/0015-agent-v2-explorer-and-tiers.md): a **strong** model runs every scenario (explores,
plans, writes templates, judges, decides the outcome); a **light** model does narrow, repetitive tasks that the strong
model hands out as tools.

| Tier | Env var | Falls back to |
|---|---|---|
| strong | `WATCHTOWER_MODEL_STRONG` | `WATCHTOWER_MODEL`, then `gpt-5.6-luna` |
| light | `WATCHTOWER_MODEL_LIGHT` | the strong model |

`WATCHTOWER_MODEL` alone sets both. Budgets per scenario:

| Scenario | Strong | Light |
|---|---|---|
| attach | 40 turns · 600,000 tokens · 1,800 s for the run | 60 calls · 400,000 tokens · 120 s per call |
| investigate (failure) | 25 turns · 300,000 tokens · 900 s | 30 calls · 200,000 tokens · 120 s per call |
| investigate (audit) | 15 turns · 150,000 tokens · 600 s | 30 calls · 200,000 tokens · 120 s per call |

`run_scenario` puts a `LightWorker` on `ctx.light`; each light task is **one** model call with fixed instructions and
a pydantic output type, counted against the light budget (calls are reserved before they start, so tasks run in
parallel can't overrun it). A failed, timed-out or malformed light answer comes back as a sentence, never an exception.

### Light tasks (`tools/delegate.py`)

Each tool collects its input deterministically, makes the light call, checks what it can of the answer, and labels the
result `LIGHT-MODEL PROPOSAL` for the strong model to review. A light task never ends a run.

| Tool | Input it gathers | Light model answers | Checked deterministically |
|---|---|---|---|
| `delegate_describe_page(url, rendered?)` | the page's pruned outline | is this the full list, the total it shows, ≤ 5 routes to the list | every quoted text, link and total is on the page |
| `delegate_map_fields(url)` | the page's label/value pairs (toolkit) | a contract field per label, or none | field names exist; each rule extracts its value; disagreement with the synonym dictionary shown |
| `delegate_classify_notices(notice_types, examples?)` | given by the strong model | open call: yes / no / unsure, with a reason | types not asked about are dropped; unanswered ones are `unsure` |
| `delegate_verify_tenders(template, tenders?, rendered?)` | a dry run of ≤ 5 tenders and each tender's page text | per field: right / wrong / not on the page, with evidence | values found verbatim on the page are confirmed without a call; one call per tender, in parallel |

Tests script the light tier with its own `ScriptedModel` (`run_scenario(..., light_model=…)`; it defaults to the
strong `model`, as the light tier defaults to the strong model).

## Tools

| Tool | What it returns |
|---|---|
| `fetch(url, focus?)` | Plain HTTP GET. HTML → pruned outline; JSON → key shape, sample values, arrays of objects with JSONPath; files → type, size, name. `focus` (CSS or JSONPath) shows only the matches and their count: the model's selector check. Pages are cached per run. |
| `render(url, focus?)` | Headless browser. Outline of the rendered page plus a summary of each captured JSON/XHR response (so a JS app's API can be read directly). |
| `try_template(template, sample=3)` | Engine dry run. Schema errors with paths; a **list check** (up to 3 list pages: count, pagination, duplicate ids); a **sample read** of N tenders with field coverage and values; a **document check** (the first bytes of 2 document links must be a file, not a login, captcha or error page); **warnings** (pagination controls the template ignores, no documents, required fields always empty); the validator's verdict. |
| `submit_template(template, notes)` | Accepted only if this exact template's last dry run was GOOD (it is dry-run if it wasn't tried). |
| `report_blocked(reason)` · `report_unreadable(reason)` · `report_transient(reason)` | End the run with that outcome; `reason` is a plain sentence for operators. |

### Pruned outline

`prune.outline()` parses with the engine's own parser (`engine.extract.parse_html`), so a selector that matches in the
outline matches in the engine. It drops scripts, styles, SVG and the head (keeping title, `meta generator` and
`rel=next/canonical` links), joins single-child wrapper chains (`div.page > main > div.content`), keeps only useful
attributes (`href`, `name`, `value`, `rel`, `data-*`…; hidden input values cut to 20 characters) and collapses runs of
same-shaped siblings to the first two plus `… ×N more tr.row`. Table cells, `dt`/`dd` and options are never collapsed:
each column means something different. Output is capped (9,000 characters by default) with a hint to use `focus`.

## Adding tools

```python
# watchtower/agent/tools/pagination.py: any module in this package is loaded automatically
from agents import RunContextWrapper
from watchtower.agent.context import AgentContext
from watchtower.agent.registry import ATTACH_AND_INVESTIGATE, tool

@tool(scenarios=ATTACH_AND_INVESTIGATE)
async def find_pagination(ctx: RunContextWrapper[AgentContext], url: str) -> str:
    """The docstring is the description the model sees."""
```

`tools_for(scenario)` gives each scenario its tools, so no scenario code changes. Tools reach portals only through
`ctx.context.client` / `ctx.context.browser` (politeness), and return short text.

## Checking it live

```bash
uv run --env-file .env python -m watchtower.agent.smoke              # key, model, tool call, structured output
uv run --env-file .env python -m watchtower.agent.probe --mock form  # attach run on a mock portal
uv run --env-file .env python -m watchtower.agent.probe https://portal.example/   # any URL
```

`smoke` checks the light model too when it differs from the strong one. `probe` runs the [attach scenario](attach.md)
and prints the outcome, the template or reason, model calls and tokens (with how many were cached) per tier, and time. Offline tests use a scripted model
(`tests/agent/scripted.py`) and the mock portals; no key needed.

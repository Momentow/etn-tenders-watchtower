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
from watchtower.agent.runtime import Budget, run_scenario

ctx = AgentContext(portal_url, client, browser)            # PoliteClient / BrowserPool from net
run = await run_scenario("attach", SCENARIO_PROMPT, f"Portal URL: {portal_url}", ctx, budget=Budget())
run.outcome      # an ADR 0009 outcome (TemplateFound, Blocked, Unreadable, Transient, …) or None
run.stop_reason  # outcome | turns | tokens | time | no_outcome
run.requests, run.input_tokens, run.cached_tokens, run.output_tokens, run.seconds
```

- **Model and key:** `OPENAI_API_KEY` (required) and `WATCHTOWER_MODEL` (default `gpt-5.6-luna`) from the environment
  (`config.py`). `run_agent()` in `runtime.py` is the one function every model call goes through, so T-099 can meter
  usage there. Reasoning effort defaults to `low`.
- **The run ends when a tool records an outcome** (`submit_template` accepted, or a `report_*` tool). A refused
  `submit_template` returns its reasons and the model continues.
- **Budget** (`Budget(max_turns=30, max_tokens=400_000, wall_seconds=900)`): turns are model calls, tokens are summed
  over all calls, wall time covers tools too. Exceeding one stops the run with `outcome=None` and the `stop_reason`;
  it never raises.
- **Nudges:** a model that answers with plain text instead of a closing tool call is reminded, at most twice, within
  the same budget.
- **Instructions** (`prompts.py`): rules (a wrong value is worse than a missing one; no login or captcha solving; try
  first, then decide), the working method, a compact template reference and two example templates. The text is static,
  so the provider's prompt cache serves it cheaply after the first call. Scenario prompts are appended to it.

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
uv run --env-file .env python -m watchtower.agent.probe --mock form  # draft attach run on a mock portal
uv run --env-file .env python -m watchtower.agent.probe https://portal.example/   # any URL
```

`probe` prints the outcome, the template or reason, model calls, tokens (and how many were cached) and time. It uses a
draft attach prompt until the `generate_template` scenario (T-015) exists. Offline tests use a scripted model
(`tests/agent/scripted.py`) and the mock portals; no key needed.

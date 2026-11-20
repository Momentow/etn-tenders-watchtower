# 0010 — Agent runtime, tool registry and budgets

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-014

## Context
ADR 0002 makes `agent` the only package that calls a model, with tools that compute and a model that judges. ADR 0009
fixes what the scenarios return. The scenarios (T-015 attach, T-016 investigate) and the deterministic toolkit (T-022)
are built in parallel on one runtime, so they need a fixed way to run a scenario, share state between tools, add tools
and stop a run.

## Decision

### Running a scenario
`watchtower.agent.runtime.run_scenario(scenario, scenario_prompt, task, ctx, *, budget, reasoning="low") -> ScenarioRun`

- `scenario` is `"attach"` or `"investigate"`; it selects the tools (`registry.tools_for`).
- Instructions are `prompts.BASE_INSTRUCTIONS` (rules, method, template reference) + two example templates + the
  scenario's own prompt. Scenario prompts never repeat the base rules.
- The run **ends when a tool records `ctx.outcome`** (an ADR 0009 outcome type). Text answers without an outcome get
  at most two reminders. `ScenarioRun` carries the outcome (or `None`), `stop_reason`
  (`outcome | turns | tokens | time | no_outcome`) and usage (model calls, input/cached/output tokens, seconds).
- **Budget** `Budget(max_turns, max_tokens, wall_seconds)` (defaults 30 / 400 000 / 900 s) is enforced across the
  whole scenario including reminders. Running out never raises; the scenario maps `outcome=None` onto its own failure
  outcome (e.g. `Unreadable`/`Unrepairable` with a reason naming the exhausted budget).
- `run_agent()` is the single function that calls the model (T-099 meters there). Tests pass `model=` a scripted
  `agents.models.interface.Model`; then no key is needed.

### Tool context
`watchtower.agent.context.AgentContext`: `portal_url`, `client` (`PoliteClient`), `browser` (`BrowserPool | None`),
`history` (`PortalHistory`, empty for attach), `pages` (per-run fetch cache), `tried` (dry-run verdicts by canonical
template JSON), `outcome`, and `extra` (scenario-specific data, e.g. the failing `ReadResult` and `Health` for
investigate). Tools reach portals only through `client`/`browser`.

### Tool registry
`@registry.tool(scenarios=ATTACH | INVESTIGATE)` on an async function `(ctx: RunContextWrapper[AgentContext], …) -> str`
in any module of `watchtower/agent/tools/`; every module there is imported automatically. The docstring is the model's
description. Names are unique. Tools return compact text (a few thousand characters at most), never raw pages.

### Core tools (T-014)
`fetch`, `render`, `try_template`, `submit_template`, `report_blocked`, `report_unreadable`, `report_transient`
(see [agent runtime](../agent/runtime.md)). `submit_template` accepts only a template whose dry run is GOOD; a dry run
is GOOD when the list reads tenders without errors, the sample reads tenders with titles, the first-read validation
passes and sampled document links return files (not login/captcha/error pages). Warnings (ignored pagination
controls, no documents, empty required fields) don't block; the prompt requires fixing or ruling them out.
T-016 adds its own closing tools for `Healthy`, `Gone` and `Unrepairable`, and `Changed` comes from `submit_template`.

### Engine helper
`engine.executor.first_list_page(template, client, browser)` (new public function) returns the first list page as
`run_template` reaches it, for the dry run's diagnostics.

## Consequences
- T-015/T-016 are a scenario prompt plus a thin wrapper: build `AgentContext`, call `run_scenario`, map the outcome.
- T-022 adds tools as new modules; no scenario or runtime change.
- Token cost per run is visible from the start (`ScenarioRun`), so T-099 only has to store it.

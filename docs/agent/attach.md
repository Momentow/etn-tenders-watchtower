# Attach: `generate_template`

`watchtower.agent.attach` is the attach scenario (T-015): given only a portal URL, the agent learns how to read it and
returns a validated template, or says plainly why it can't. It runs on the [agent runtime](runtime.md) with the attach
tools; the [read pipeline](../core/pipeline.md) calls it for `attach` jobs and only promotes the template after its own
read passes validation ([ADR 0009](../decisions/0009-pipeline-and-agent-outcomes.md)).

## What the agent is told
On top of the shared rules and method, the attach prompt (`ATTACH_PROMPT`) asks for:

- **The real tender list.** The URL may be a home page, a search page, a single notice, or a summary page that only
  links on to the tender page. The agent finds the list of open tenders first and adds a `detail.resolve` step when
  list entries land on a summary page.
- **Open tenders, every page.** If the portal only lists everything, a `status` rule marks closed and withdrawn ones.
- **Try first, then decide.** `report_blocked` only after a dry run or a direct fetch shows that the list or the
  documents really need a login or a solved captcha.

## Outcomes
The run ends with a closing tool call; `run_attach` maps it onto an `AttachOutcome`:

| Run ended with | Outcome |
|---|---|
| `submit_template` accepted (its dry run was GOOD) | `TemplateFound` |
| `report_blocked`, but a template already passed the dry run, documents included | `TemplateFound` with that template |
| `report_blocked` | `Blocked` → the portal fails with the reason |
| `report_unreadable` | `Unreadable` with the reason |
| `report_transient` | `Unreadable`: "the portal could not be read right now (…)" |
| turn, token or time budget exhausted, or no closing call | `Unreadable` naming what ran out |
| `OPENAI_API_KEY` missing | `Unreadable`: "the agent is not configured (…)" |

The block override is deterministic: a GOOD dry run means the list was read and sampled document links returned real
files, so a captcha or login box on the page didn't stop delivery.

Budget: 30 model calls, 400 000 tokens, 15 minutes (`ATTACH_BUDGET`). Each run logs model calls, tokens (cached
included) and time.

## Wiring
`app.py` passes `make_generate_template(client, browser)` to `pipeline.configure`, so the scenario shares the process's
polite client and browser. `investigate` stays a stub until T-016.

## Trying it
```bash
uv run --env-file .env python -m watchtower.agent.probe --mock ssr        # serves the mock portals itself
uv run --env-file .env python -m watchtower.agent.probe https://portal.example/
```

`probe` prints the raw run (stop reason, model calls, tokens, time) and the outcome the pipeline would get. Offline
tests (`tests/agent/test_attach.py`) drive the scenario with a scripted model on the mock portals, including a full
`attach` job through the pipeline.

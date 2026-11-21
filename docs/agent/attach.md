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
  documents really need a login or a solved captcha. The tool checks the evidence itself ([truthful blocks](blocks.md)):
  two tenders, the session, anonymous routes.

### Explore like a person (T-034)
The prompt also teaches the explorer method of [ADR 0015](../decisions/0015-agent-v2-explorer-and-tiers.md), with the
[explorer tools](runtime.md#explorer-tools):

1. Find the page a person would use to see **all** current tenders, and the portal's own total. A home-page widget
   with the latest N tenders is not the list.
2. Make the list complete: pagination, *load more*, scrolling.
3. Open at least two tenders and their tabs.
4. Read `network`; `replay` the call that carries the tenders. "Same data without a browser" → a `kind: api`
   template (list, detail and documents from JSON calls), checked for paging with `replay(call, params=…)`. A header
   the call needs becomes a `session` capture. A browser `flow` only when there's no usable API.
5. Keep only open calls for competition: the API's own filters and `scope` (`delegate_classify_notices` helps).
6. Write the template, dry-run, fix, submit.

The prompt carries a compact [template v2](../engine/template-v2.md) reference and the tenderned.nl API-mode template
as a worked example. The agent writes v2 when it needs an entry deeper than the portal URL, clicks, *load more*, tabs,
an API from the network log, a session or scope; otherwise v1. `TemplateFound` carries either format, and the pipeline
stores and runs both. Investigate and audits still repair v1 templates until T-037.

## Outcomes
The run ends with a closing tool call; `run_attach` maps it onto an `AttachOutcome`:

| Run ended with | Outcome |
|---|---|
| `submit_template` accepted (its dry run was GOOD) | `TemplateFound` |
| `report_blocked`, but a template already passed the dry run, documents included | `TemplateFound` with that template |
| `report_blocked` with the evidence [truthful blocks](blocks.md) needs | `Blocked` → the portal fails with the reason |
| `report_blocked` refused, then the run ends without an outcome | `TemplateFound` with a template whose dry run was GOOD; else `Unreadable`: the block was suspected but not confirmed, and why |
| `report_transient` while the firewall answers 403 on every path | `Blocked`: access refused by the portal's firewall |
| `report_unreadable` | `Unreadable` with the reason |
| `report_transient` | `Unreadable`: "the portal could not be read right now (…)" |
| turn, token or time budget exhausted, or no closing call | `Unreadable` naming what ran out |
| `OPENAI_API_KEY` missing | `Unreadable`: "the agent is not configured (…)" |

The block override is deterministic: a GOOD dry run means the list was read and sampled document links returned real
files, so a captcha or login box on the page didn't stop delivery.

Budget: strong 40 model calls, 600 000 tokens, 30 minutes for the run; light 60 calls, 400 000 tokens
(`ATTACH_BUDGET`, [model tiers](runtime.md#model-tiers)). Each run logs model calls, tokens (cached
included) and time.

## Wiring
`app.py` passes `make_generate_template(client, browser)` to `pipeline.configure`, so the scenario shares the process's
polite client and browser, next to `make_investigate` (T-016).

## Trying it
```bash
uv run --env-file .env python -m watchtower.agent.probe --mock ssr        # serves the mock portals itself
uv run --env-file .env python -m watchtower.agent.probe https://portal.example/
```

`probe` prints the raw run (stop reason, model calls, tokens, time) and the outcome the pipeline would get. Offline
tests (`tests/agent/test_attach.py`) drive the scenario with a scripted model on the mock portals, including a full
`attach` job through the pipeline.

## Live runs on the mock portals
Measured on 2026-11-20 with the default model (`uv run --env-file .env python -m watchtower.agent.probe --mock <kind>`):

| Kind | Outcome | Model calls | Tokens in (cached) / out | Time | What the template does |
|---|---|---|---|---|---|
| `ssr` | `TemplateFound` | 6 | 27 415 (21 245) / 1 515 | 27 s | HTML list, `next_link` pagination, detail fields |
| `js` | `TemplateFound` | 5 | 23 084 (16 942) / 1 478 | 29 s | JS-rendered list |
| `multistep` | `TemplateFound` | 11 | 62 802 (54 270) / 1 991 | 54 s | `detail.resolve` follows the summary page to the real tender |
| `form` | `TemplateFound` | 11 | 56 551 (48 725) / 2 037 | 46 s | search-form list |
| `captcha` | `TemplateFound` | 10 | 49 262 (42 454) / 1 381 | 31 s | `render: true`; the list and documents load without solving the captcha |
| `login` | `Blocked` | 8 | 38 641 (26 686) / 606 | 25 s | documents redirect to a login page |
| `robot` | `Blocked` | 6 | 28 594 (22 486) / 809 | 25 s | document links redirect to a reCAPTCHA form |

Every run stayed far below `ATTACH_BUDGET` (at most 11 of 30 calls and 65k of 400k tokens).

## Live runs with the explorer (T-034)
Measured on 2026-11-21 with the default model, before T-036's completeness checks:

| Portal | Outcome | Model calls | Tokens in (cached) / out | Time | Result |
|---|---|---|---|---|---|
| mock `app` (first run) | `TemplateFound`, v2 API mode | 10 | 103 927 (90 532) / 2 738 | 72 s | Found the API, but guessed procedure codes, kept an early-terminated call and missed the withdrawn status |
| mock `app` (after the prompt asked for every code and status flag) | `TemplateFound`, v2 API mode | 14 | 160 208 (144 509) / 3 135 | 96 s | `open` → click → `network` → `replay`; all 21 open calls with the right fields and every document. One wrong value left: a withdrawn call read as `open` (no `status` rule) |
| tenderned.nl | `TemplateFound`, v2 API mode | 10 | 126 187 (106 730) / 2 305 | 166 s | Found `/papi/…/publicaties` through the browser and replay. Incomplete: it kept the page's "published in the last 30 days" filter instead of `publicatieType=AAO` and a deadline from `{{today}}`, so notice types aren't filtered and older open calls are missed |

The live **scripted** run on tenderned.nl (open → click *Alle aankondigingen* → `network` → `replay`, page 2 → submit
the hand-written template) took 112 s, and `replay` answered "SAME DATA WITHOUT A BROWSER", 5 of 5 records on the page.

What's still missing is what T-036 checks: the portal's declared total versus the delivered count, notice types the
records carry but `scope` ignores, and code values a `map` doesn't cover. The light tier wasn't used in any run;
the model skipped `delegate_verify_tenders`, which would have flagged the withdrawn status.

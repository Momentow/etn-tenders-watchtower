"""Scenario `generate_template` (T-015, ADR 0002/0009/0010): learn how to read a new portal.

The model explores the portal with the attach tools and ends the run with `submit_template` (accepted only after a GOOD
dry run), `report_blocked`, `report_unreadable` or `report_transient`. This wrapper maps every way a run can end onto an
`AttachOutcome`:

| Run ended with | Outcome |
|---|---|
| an accepted template | `TemplateFound` |
| `report_blocked` after a template passed the dry run, documents included | `TemplateFound` with it (try first) |
| `report_blocked` with the evidence `blocks.review` needs | `Blocked` |
| `report_blocked` refused, then the run ends, after a GOOD dry run | `TemplateFound` with it |
| `report_blocked` refused, then the run ends | `Unreadable`: the block wasn't confirmed, and why |
| `report_unreadable` / `report_transient` | `Unreadable` with the reason |
| budget exhausted or no closing call | `Unreadable` naming what ran out |
| no model key configured | `Unreadable` saying so |

`make_generate_template(client, browser)` returns the function the pipeline calls (`GenerateTemplate` protocol).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from agents import Model

from watchtower.agent.config import AgentConfigError
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import (
    AttachOutcome,
    Blocked,
    GenerateTemplate,
    TemplateFound,
    Transient,
    Unreadable,
)
from watchtower.agent.runtime import Budget, ScenarioRun, TieredBudget, run_scenario
from watchtower.engine.examples import _DIR_V2 as EXAMPLES_V2_DIR
from watchtower.engine.template_v2 import AnyTemplate, load_template
from watchtower.net import BrowserPool, PoliteClient

log = logging.getLogger(__name__)

# ADR 0015: exploring at 1 req/s is slow, and light checks run inside the strong model's wall time.
ATTACH_BUDGET = TieredBudget(
    strong=Budget(max_turns=40, max_tokens=600_000, wall_seconds=1800),
    light=Budget(max_turns=60, max_tokens=400_000, wall_seconds=120),
)

ATTACH_PROMPT = """\
Attach a new portal. You get only its URL. Produce a template that reads EVERY open tender on the portal (all list
pages) with its contract fields and ALL its documents.

- The URL may be a home page, a search page, a single notice, or a summary/notice page that only links on to the real
  tender page. Find the portal's list of current (open) tenders first; if list entries lead to a summary page, add a
  `detail.resolve` follow step to reach the real tender page with the documents.
- Prefer a list of open/current tenders. If the portal only lists everything, keep the list and add a `status` rule so
  closed and withdrawn tenders are marked.
- Captcha or login boxes: try first, then decide. Only call `report_blocked` after a dry run (or a direct `fetch`)
  shows that the list or the documents really need a login or a solved captcha. A captcha widget on a page whose list
  and document links work without it is NOT a block.
- `report_unreadable` only when there is no public tender list at all (or nothing you can express as a template), and
  `report_transient` when the portal is down or answering with server errors.

End with exactly one call: `submit_template` (verdict GOOD), `report_blocked`, `report_unreadable` or
`report_transient`.

# Explore like a person (one browser session for the whole run)
`open` a page, read its snapshot, `act` on it (click the link to all tenders, "load more", open a tender, switch a
tab) and read `network`: the API calls the page made. `fetch`/`render` stay fine for a quick look at a plain page.
1. Find the page a person would use to see ALL current tenders, and the portal's own total ("2,474 results"). A home
   page widget with the latest N tenders is not the list.
2. Make the list complete: pagination, "load more", scrolling.
3. Open at least two tenders and their tabs: find each contract field and the documents.
4. Read `network`. When a call returns the tender records, `replay` it. "SAME DATA WITHOUT A BROWSER" → write
   `list.kind: api` (and `detail`/`documents` from their JSON calls too): the cheapest and most stable read, no browser.
   Check paging with `replay(call, params={"page": "2"})` and use `network(show=…)` for the exact request. Prefer the
   API's own filters (notice type, a deadline from {{today}}). If replay says a header is NEEDED, add a `session`
   that captures it. Use `kind: page` with a browser `flow` only when there is no usable API.
5. If the list mixes notice types (awards, prior notices, corrections…), decide which are open calls for competition
   (`delegate_classify_notices` helps) and keep only those with the API's filters and `scope`.
6. Codes in the data: before writing a `map`, list the distinct values the records really hold (`fetch` the API
   with a JSONPath `focus` such as `$.content[*].procedure.code`) and map every one; an unmapped value is dropped.
   Status codes (published / withdrawn / closed) go into `status`; flags that end or cancel a call (e.g.
   `isVroegtijdigeBeeindiging`, "cancelled") into `scope.exclude` or `status.withdrawn`.
7. Write the template, `try_template`, fix, repeat; `delegate_verify_tenders` on 3–5 tenders; `submit_template`.
   Write format v2 when you need any of: an entry deeper than the portal URL, clicks/load more/tabs, an API from
   the network log, a session, or scope; otherwise v1.

# Template format v2 ("format_version": 2; unknown keys are rejected)
{"format_version": 2, "timezone", "country"?, "software"?, "notes",
 "entry": {"url"}  where reading starts, e.g. the list page itself,
 "session"?: {"via": "http"|"browser", "steps": [Request...] (http) | [Action...] (browser),
   "capture": [{"name", "from": "cookie"|"request_header"|"response_header"|"response_json", "match": URL regex,
   "key": cookie or header name ("*" = all cookies) | JSONPath}]}   → use as {{session.<name>}} in any request,
 "list": {"kind": "api", "request": Request, "items": "$.content[*]", "total"?: "$.totalElements",
          "pagination": none | param | cursor | next_link (as v1)}
       | {"kind": "page", "via": "browser"(default)|"http", "flow": [Action...] (run after opening entry),
          "items": Extract(css|xpath) + "group"?: {"size": N} | {"starts_at": css} (one tender over sibling rows),
          "total"?: Extract on the page, "pagination": none | next_link | param | {"kind": "click", "target"} |
          {"kind": "load_more", "target", "max_clicks"} | {"kind": "scroll", "times"}},
 "detail"?: {"kind": "api", "request": Request with {field} placeholders, "page_url": "https://…/{id}" (the tender's
            public page: required unless fields.url is given)}
          | {"kind": "page", "link": Extract | "url_template", "via": "http"(default)|"browser",
            "views"?: {"docs": [Action...]} (browser only; fields/documents then use "on": "detail.docs"),
            "resolve"?: [FollowStep...] (http)},
 "fields": as v1 (JSONPath for api scopes, css/xpath for pages; "on": "item"|"detail"|"detail.<view>"),
 "documents"?: as v1, plus "request": Request (a JSON call listing the files, {id} placeholders) and
   "on": "detail.<view>",
 "status"?: as v1,
 "scope"?: {"include": [Condition...], "exclude": [Condition...], "open_by_deadline": true}
   Condition = {"source": Extract on the list item, "in": [values] | "matches": regex | "equals": value},
 "urls"?: {"drop_params": [], "drop_matrix": ["jsessionid"], "drop_numeric_query": false}}
Request = {"url", "method": "GET"|"POST", "params": {}, "headers": {}, "form"?: {}, "json"?: body}; placeholders
{{today}} (YYYY-MM-DD in the template's zone), {{session.<name>}}; Cookie/Authorization headers only as {{session.*}}.
Action = {"do": "goto"|"click"|"fill"|"select"|"press"|"wait_for"|"load_more"|"scroll"|"tab", "target"?: {"role",
"name"} | {"text"} | {"css"} | {"xpath"}, …} exactly as `act` takes them. Every request stays on the portal's site.
"""


def _v2_example() -> str:
    """tenderned.nl in API mode (hand-written, T-033): the shape a good explorer run ends with."""
    data = json.loads((EXAMPLES_V2_DIR / "tenderned.json").read_text(encoding="utf-8"))
    data.pop("notes", None)
    return "\n## Example v2 template: tenderned.nl, API mode\n" + json.dumps(
        data, ensure_ascii=False, separators=(",", ":")
    )


ATTACH_PROMPT += _v2_example()


@dataclass
class AttachRun:
    outcome: AttachOutcome
    run: ScenarioRun | None  # None when no model run happened (e.g. no key)


async def run_attach(
    portal_url: str,
    client: PoliteClient,
    browser: BrowserPool | None,
    *,
    budget: Budget | TieredBudget = ATTACH_BUDGET,
    model: Model | None = None,
    light_model: Model | None = None,
) -> AttachRun:
    ctx = AgentContext(portal_url, client, browser)
    try:
        task = f"Portal URL: {portal_url}"
        run = await run_scenario(
            "attach", ATTACH_PROMPT, task, ctx, budget=budget, model=model, light_model=light_model
        )
    except AgentConfigError as e:
        return AttachRun(Unreadable(reason=f"the agent is not configured ({e})"), None)
    log.info(
        "attach %s: %s after %d model calls (%d light), %d+%d tokens (%d cached), %.0fs",
        portal_url,
        run.stop_reason,
        run.requests,
        run.by_tier["light"].requests,
        run.input_tokens,
        run.output_tokens,
        run.cached_tokens,
        run.seconds,
    )
    return AttachRun(_outcome(run, ctx), run)


def make_generate_template(
    client: PoliteClient,
    browser: BrowserPool | None,
    *,
    budget: Budget | TieredBudget = ATTACH_BUDGET,
    model: Model | None = None,
) -> GenerateTemplate:
    """The `generate_template(portal_url)` the pipeline calls, bound to the process's network objects."""

    async def generate_template(portal_url: str) -> AttachOutcome:
        return (await run_attach(portal_url, client, browser, budget=budget, model=model)).outcome

    return generate_template


def _outcome(run: ScenarioRun, ctx: AgentContext) -> AttachOutcome:
    outcome = run.outcome
    if isinstance(outcome, TemplateFound):
        return outcome
    if isinstance(outcome, Blocked):
        if (good := _good_template(ctx)) is not None:
            log.info("attach %s: reported blocked, but a template passed the dry run; using it", ctx.portal_url)
            return TemplateFound(
                template=good,
                notes=f"The model reported a block ({outcome.reason}), but this template read the list and "
                "downloaded documents without it.",
            )
        return outcome
    if isinstance(outcome, Unreadable):
        return outcome
    if isinstance(outcome, Transient):
        return Unreadable(reason=f"the portal could not be read right now ({outcome.reason.rstrip('.')})")
    if outcome is not None:  # an investigate-only outcome: not an answer to attach
        return Unreadable(reason=f"the attach ended with an unexpected {outcome.kind} report")
    if (refused := ctx.blocks.refused) is not None:
        if (good := _good_template(ctx)) is not None:
            log.info("attach %s: block report refused, and a template passed the dry run; using it", ctx.portal_url)
            return TemplateFound(
                template=good,
                notes=f"The model reported a block ({refused}.), but the evidence didn't confirm it and this "
                "template read the list and downloaded documents.",
            )
        why = ctx.blocks.refused_why.split("; ")[0]
        return Unreadable(reason=f"a login or captcha block was suspected ({refused}) but not confirmed: {why}")
    return Unreadable(reason=_budget_reason(run.stop_reason))


def _good_template(ctx: AgentContext) -> AnyTemplate | None:
    """The last template whose dry run was GOOD (ctx.tried keeps insertion order)."""
    good = [key for key, ok in ctx.tried.items() if ok]
    return load_template(good[-1]) if good else None


def _budget_reason(stop: str) -> str:
    what = {
        "turns": "the turn limit",
        "tokens": "the token budget",
        "time": "the time limit",
    }.get(stop)
    if what:
        return f"no working template was found within {what} of the attach"
    return "the agent stopped without finding a working template"

"""Explorer tools (T-034, ADR 0015): browse a portal the way a person does, in one browser session per run.

`open` and `act` drive the run's session (ADR 0014 actions); `snapshot` shows the current page; `network` lists the
XHR/fetch calls the page made; `replay` re-sends one of them over the polite client, without the browser, and says
whether it returns the same records. "Same data without a browser" is the signal to write a `kind: api` template.
Every request, browser sub-requests included, still waits for the per-host limiter.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agents import RunContextWrapper
from pydantic import TypeAdapter, ValidationError

from watchtower.agent.context import AgentContext, Fetched
from watchtower.agent.pages import _record_page_block, clip, short_err
from watchtower.agent.prune import summarize_json
from watchtower.agent.registry import ATTACH, tool
from watchtower.net import Action, ActionResult, BrowserSession, Exchange, SessionBudgetExceeded
from watchtower.net.actions import Goto

Ctx = RunContextWrapper[AgentContext]

SNAPSHOT_CHARS = 6000
ACT_SNAPSHOT_CHARS = 3500
NETWORK_LIMIT = 15
# Headers a replay leaves out: the client sets its own, or they belong to the browser's connection.
_DROPPED_HEADERS = {"host", "content-length", "cookie", "connection", "accept-encoding", "user-agent", "origin"}
# Headers that don't carry a session: a replay that needs only these needs no `session` in the template.
_PLAIN_HEADERS = {"accept", "accept-language", "content-type", "referer", "cache-control", "pragma"}
_ACTION = TypeAdapter(Action)


async def _session(c: AgentContext) -> BrowserSession | str:
    try:
        return await c.browser_session()
    except Exception as e:  # no browser, or Chromium failed to start
        return f"No browser session: {short_err(e)}. Use `fetch` instead."


async def _act(c: AgentContext, action: Action) -> tuple[ActionResult | None, str]:
    s = await _session(c)
    if isinstance(s, str):
        return None, s
    try:
        r = await s.act(action)
    except SessionBudgetExceeded as e:
        return None, f"The run's browser session is used up ({e}). Work with what the network log already shows."
    requested = action.url if isinstance(action, Goto) else r.url
    _record_page_block(c, requested, Fetched(r.url, r.status, "text/html", await s.html()))
    return r, ""


def _calls(network: list[Exchange]) -> list[str]:
    return [_exchange_line(x) for x in network]


def _exchange_line(x: Exchange) -> str:
    size = f"{len(x.body):,} B" if x.body is not None else "binary/no body"
    body = f" body={clip(x.request_body, 120)}" if x.request_body else ""
    return f"#{x.id} {x.method} {clip(x.url, 200)}{body} → {x.status} {x.content_type or '?'} {size}"


async def _report(c: AgentContext, r: ActionResult, max_chars: int) -> str:
    head = f"{r.action} → {r.url}" + (f" (HTTP {r.status})" if r.status else "")
    lines = [head]
    if r.error:
        lines.append(f"ERROR: {r.error}")
    if r.note:
        lines.append(f"note: {r.note}")
    lines.append(f"time: {r.seconds_active:.1f}s active, {r.seconds_waiting:.1f}s waiting for the rate limiter")
    calls = _calls(r.network)
    lines.append(f"network calls this caused: {len(calls)}" + ("" if calls else " (none)"))
    lines += ["  " + x for x in calls[-NETWORK_LIMIT:]]
    if c.session is not None:
        lines.append(clip(await c.session.snapshot(), max_chars))
    return "\n".join(lines)


@tool(scenarios=ATTACH)
async def open(ctx: Ctx, url: str) -> str:  # noqa: A001 - the tool's name is what the model sees
    """Open a URL in the run's browser session (JavaScript runs, cookies and app state are kept for later actions)
    and show the page snapshot and the API calls the page made. Use it to explore like a person: then `act` on
    what you see, and read the calls with `network`/`replay`."""
    r, err = await _act(ctx.context, Goto(do="goto", url=url))
    return err or await _report(ctx.context, r, SNAPSHOT_CHARS)


@tool(scenarios=ATTACH)
async def snapshot(ctx: Ctx) -> str:
    """The current page of the browser session: headings, tabs, buttons, links, form fields, repeated blocks with
    counts and visible totals ("2,474 results")."""
    s = await _session(ctx.context)
    if isinstance(s, str):
        return s
    return clip(await s.snapshot(), SNAPSHOT_CHARS)


@tool(scenarios=ATTACH)
async def act(ctx: Ctx, action: str) -> str:
    """Do one action in the browser session; `action` is JSON, the same actions a v2 template's `flow` uses:
    {"do":"click","target":{"role":"link","name":"Alle aankondigingen"}} · {"do":"load_more","target":{"role":
    "button","name":"Laad meer"},"max_clicks":5} · {"do":"tab","target":{"role":"tab","name":"Documenten"}} ·
    {"do":"fill","target":{"css":"input[name=q]"},"value":"…"} · {"do":"press","target":…,"key":"Enter"} ·
    {"do":"select",…,"value":"…"} · {"do":"wait_for","text":"…"} · {"do":"scroll"} · {"do":"goto","url":"…"}.
    A target is one of {role,name} | {text} | {css} | {xpath}, plus optional nth. Returns the new URL, any error, the
    API calls the action caused, and the new snapshot."""
    try:
        parsed = _ACTION.validate_json(action)
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors()[:4])
        return f"Not a valid action: {errs}"
    r, err = await _act(ctx.context, parsed)
    return err or await _report(ctx.context, r, ACT_SNAPSHOT_CHARS)


@tool(scenarios=ATTACH)
async def network(ctx: Ctx, filter: str | None = None, show: int | None = None) -> str:  # noqa: A002
    """The XHR/fetch and JSON calls the browser session made, newest first (`filter`: a substring of the URL).
    `show`: one call's full request (method, URL, headers, body) and the shape of its JSON response, with the JSONPath
    of its arrays of objects: what you need to write a `kind: api` list or detail."""
    c = ctx.context
    if c.session is None:
        return "No browser session yet: `open` a page first."
    if show is not None:
        if not 0 <= show < len(c.session.network):
            return f"No call #{show}; the log has #0 to #{len(c.session.network) - 1}."
        return _show(c.session.network[show])
    calls = [x for x in reversed(c.session.network) if not filter or filter in x.url]
    if not calls:
        return "No calls" + (f" matching {filter!r}" if filter else "") + " so far."
    lines = [f"{len(calls)} call(s)" + (f" matching {filter!r}" if filter else "") + ", newest first:"]
    for x in calls[:NETWORK_LIMIT]:
        lines.append(_exchange_line(x) + f"  [action {x.action_index}]")
        if (sample := _json_sample(x.body)) is not None:
            lines.append("    " + sample)
    if len(calls) > NETWORK_LIMIT:
        lines.append(f"… {len(calls) - NETWORK_LIMIT} older; narrow with `filter`")
    return "\n".join(lines)


def _show(x: Exchange) -> str:
    lines = [_exchange_line(x), "request headers: " + json.dumps(_kept_headers(x), ensure_ascii=False)]
    if x.request_body:
        lines.append("request body: " + clip(x.request_body, 1500))
    value = _json(x.body)
    if value is None:
        lines.append("response: " + (clip(x.body, 800) if x.body else "(no text body)"))
    else:
        lines.append(
            "response" + (" (truncated)" if x.truncated else "") + ":\n" + summarize_json(value, max_chars=3000)
        )
    return "\n".join(lines)


def _json_sample(body: str | None) -> str | None:
    value = _json(body)
    if value is None:
        return None
    path, records = _records(value)
    if records:
        keys = list(dict.fromkeys(k for r in records[:3] for k in r))[:12]
        return f"{path}[*]: {len(records)} records, keys {', '.join(keys)}"
    return clip(json.dumps(value, ensure_ascii=False), 160)


# --------------------------------------------------------------------------- replay


@tool(scenarios=ATTACH)
async def replay(ctx: Ctx, call: int, params: str | None = None, body: str | None = None) -> str:
    """Re-send a call from `network` over plain HTTP (no browser, no cookies) and compare its records with what the
    page received and shows. `params`: JSON object of query parameters to set, e.g. {"page": "1", "size": "50"} to
    check pagination; `body`: a replacement JSON request body. "SAME DATA WITHOUT A BROWSER" means a `kind: api`
    template can read this call directly; the answer also says whether the call needs a header the page obtained
    (then the template needs a `session` that captures it)."""
    c = ctx.context
    if c.session is None or not 0 <= call < len(c.session.network):
        return "Unknown call: list the calls with `network` first."
    x = c.session.network[call]
    try:
        changes = json.loads(params) if params else {}
        new_body = json.loads(body) if body else None
    except ValueError as e:
        return f"`params` and `body` must be JSON: {e}"
    if not isinstance(changes, dict):
        return '`params` must be a JSON object, e.g. {"page": "2"}'
    url = _with_params(x.url, {str(k): str(v) for k, v in changes.items()})
    headers = _kept_headers(x)
    content = json.dumps(new_body) if new_body is not None else x.request_body

    first = await _send(c, x.method, url, headers, content)
    if isinstance(first, str):
        return f"REPLAY FAILED: {first}"
    status, ctype, text = first
    lines = [f"REPLAY {x.method} {clip(url, 200)} → {status} {ctype or '?'} {len(text):,} B"]
    page_value = _json(x.body)
    value = _json(text)
    if status >= 400 or value is None:
        lines.append(_why_not(status, text, x))
        return "\n".join(lines)

    path, records = _records(value)
    _, page_records = _records(page_value) if page_value is not None else ("", [])
    if not records:
        lines.append("The response has no array of objects: " + clip(json.dumps(value, ensure_ascii=False), 300))
        return "\n".join(lines)
    lines.append(f"records: {path}[*], {len(records)} (the page's own call had {len(page_records)})")
    same = bool(page_records) and _canon(records[:3]) == _canon(page_records[:3])
    if not changes and new_body is None:
        lines.append("first records identical to the page's call: " + ("yes" if same else "NO"))
    else:
        lines.append(
            "first records differ from the page's call (expected after a page change): "
            + ("yes" if not same else "NO, the change had no effect")
        )
    shown = await _shown_on_page(c, records[:5])
    if shown is not None:
        lines.append(f"values from the first records found in the page's text: {shown}")

    session_headers = [h for h in headers if h.lower() not in _PLAIN_HEADERS]
    if session_headers:
        bare = {k: v for k, v in headers.items() if k not in session_headers}
        second = await _send(c, x.method, url, bare, content)
        ok_without = not isinstance(second, str) and second[0] < 400 and _records(_json(second[2]))[1]
        if ok_without:
            lines.append(f"headers {', '.join(session_headers)} are not needed (it works without them).")
        else:
            why = second if isinstance(second, str) else f"HTTP {second[0]}"
            lines.append(
                f"NEEDS headers {', '.join(session_headers)} (without them: {why}). Capture them in a template "
                "`session` (via browser, from the page's own requests) and send them as {{session.<name>}}."
            )
    if not changes and new_body is None and same:
        lines.append("SAME DATA WITHOUT A BROWSER: a `kind: api` list on this call should work.")
    return "\n".join(lines)


async def _send(c: AgentContext, method: str, url: str, headers: dict[str, str], content: str | None):
    try:
        r = await c.client.request(method, url, headers=headers, content=content)
    except Exception as e:  # network errors, a deleted portal, a refused redirect
        return short_err(e)
    return r.status_code, r.headers.get("content-type", ""), r.text


def _why_not(status: int, text: str, x: Exchange) -> str:
    hint = clip(re.sub(r"\s+", " ", text), 200)
    if status in (401, 403):
        return (
            f"Refused without the browser (HTTP {status}): the call probably needs the page's cookie or a token. "
            f"Response: {hint}"
        )
    return f"Not JSON records (HTTP {status}): {hint}"


def _kept_headers(x: Exchange) -> dict[str, str]:
    return {
        k: v
        for k, v in x.request_headers.items()
        if k.lower() not in _DROPPED_HEADERS and not k.startswith(":") and not k.lower().startswith("sec-")
    }


def _with_params(url: str, changes: dict[str, str]) -> str:
    if not changes:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(changes)
    return urlunsplit(parts._replace(query=urlencode(query)))


async def _shown_on_page(c: AgentContext, records: list[dict[str, Any]]) -> str | None:
    """How many of these records have a distinctive text value that the current page shows."""
    if c.session is None or not records:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", await c.session.html())).lower()
    hits = 0
    for r in records:
        values = [v for v in r.values() if isinstance(v, str) and 8 <= len(v) <= 200 and not v.startswith("http")]
        if any(re.sub(r"\s+", " ", v).lower() in text for v in values):
            hits += 1
    return f"{hits} of {len(records)}"


# --------------------------------------------------------------------------- JSON helpers


def _json(text: str | None) -> Any:
    if not text or text.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _records(value: Any, path: str = "$", depth: int = 0) -> tuple[str, list[dict[str, Any]]]:
    """The largest array of objects in a JSON value, with its JSONPath."""
    best: tuple[str, list[dict[str, Any]]] = (path, [])
    if depth > 6:
        return best
    if isinstance(value, list) and value and isinstance(value[0], dict):
        best = (path, [v for v in value if isinstance(v, dict)])
    children = value.items() if isinstance(value, dict) else []
    for k, v in children:
        key = f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) else f"{path}['{k}']"
        found = _records(v, key, depth + 1)
        if len(found[1]) > len(best[1]):
            best = found
    return best


def _canon(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, sort_keys=True, ensure_ascii=False)

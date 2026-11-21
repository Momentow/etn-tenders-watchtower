# Network layer (`watchtower.net`)

The only way any module talks to a portal ([ADR 0002](../decisions/0002-architecture.md)). It enforces the brief's
politeness rule: **at most 1 request per second per portal host**, across every worker, HTTP and browser alike.

## Setup: one limiter per process

```python
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient

limiter = HostRateLimiter()            # 1.0 s per host
client = PoliteClient(limiter=limiter)
browser = BrowserPool(limiter=limiter)  # Chromium starts on first render
...
await client.aclose(); await browser.aclose()
```

Create these once (at app startup) and pass them to the engine, documents and agent tools. Two limiters would mean two
budgets per host, so never build a second one.

## `HostRateLimiter`
- Keyed by lower-cased host name, port ignored (`Portal.example:8443` and `portal.example` share a budget).
- Requests to one host are spaced at least `min_interval` apart and served in arrival order; other hosts don't wait.

## `PoliteClient` (httpx)
- `get(url, **kw)` returns a read `httpx.Response`. `stream(method, url, **kw)` is an async context manager for large
  downloads (the body is never fully in memory).
- **Every request waits for the limiter**, including each redirect hop and each retry (an httpx request hook does it).
- **Retries:** connection errors, timeouts and HTTP 429/500/502/503/504, up to 3 attempts, exponential backoff with
  jitter (1 s, 2 s…), or `Retry-After` when the server sends it (capped at 60 s). 4xx errors are returned at once.
  For `stream`, retries happen only before the response is handed over.
- **Timeouts:** 30 s, 10 s to connect. **User-Agent:** `WatchtowerBot/0.1 (automated public-procurement tender reader;
  +https://github.com/Momentow/etn-tenders-watchtower)`.
- It doesn't raise on HTTP errors: check `status_code`.

## `BrowserPool` (Playwright Chromium)
- `render(url)` → `RenderResult(url, status, html, responses)`: the final URL, the main document's status, the
  rendered HTML and every captured XHR/fetch or JSON response (`CapturedResponse`: url, method, status, content type,
  body as text up to 2 MB). `result.json_responses()` keeps the JSON ones, which is usually the portal's own API.
- One shared headless Chromium, at most 2 pages at once. `render` is a one-`goto` [session](#browser-sessions): a fresh
  context (no cookies), 45 s of **active** time (waiting for the rate limiter doesn't count), 600 s wall clock.
- Every request the page makes waits for the same limiter. Images, media and fonts are aborted, so they're never sent.
- Scripts and stylesheets are cached per pool (64 MB, 1 hour, not when the portal says `no-store`/`no-cache`), so the
  second boot of a JS app costs a few requests instead of dozens. Data (XHR/fetch, documents) is never cached.
- Known gap: redirect hops *inside* the browser are followed by Chromium without passing through the route hook, so
  only the first request of a redirect chain is spaced. Portal pages rarely chain more than one hop.

## Browser sessions

A session holds one browser context across several actions, the way a person browses: cookies, storage and app state
carry over ([ADR 0014](../decisions/0014-template-format-v2.md)).

```python
from pydantic import TypeAdapter
from watchtower.net import Action, SessionBudget

A = TypeAdapter(Action)
async with browser.session(budget=SessionBudget()) as s:
    await s.act(A.validate_python({"do": "goto", "url": "https://www.tenderned.nl/"}))
    await s.act(A.validate_python({"do": "click", "target": {"role": "link", "name": "Alle aankondigingen"}}))
    r = await s.act(A.validate_python({"do": "load_more", "target": {"role": "button", "name": "Laad meer"}}))
    r.network        # the API calls that action caused (Exchange: method, url, headers, body, response body …)
    await s.snapshot()   # compact text view: headings, tabs, buttons, form fields, repeated blocks, links, totals
```

- **Actions** (`watchtower.net.actions`): `goto`, `click`, `fill`, `select`, `press`, `wait_for` (an element, a text
  or a response URL), `load_more` (click until the control is gone or disabled, nothing new appears, or `max_clicks`),
  `scroll`, `tab`. A `Target` is exactly one of `{role, name}`, `{text}`, `{css}`, `{xpath}`, plus `nth`. There is no
  action that runs JavaScript. The same models are used by v2 templates and agent tools.
- **`act()`** waits until the page settles: nothing in flight *or queued at the limiter* for `idle_ms` (1.5 s). It
  returns an `ActionResult`: page URL, status when it navigated, the `Exchange`s it caused, active and waiting seconds,
  and `error`/`note` (e.g. `"2 clicks; the control is gone"`). A failed action (no such element, a `wait_for` that
  never matches within 30 s of active time) comes back in `error`; the session stays usable.
- **Budget** `SessionBudget(active_seconds=120, idle_ms=1500, max_seconds=1800)`. Only an exhausted budget raises
  (`SessionBudgetExceeded`). Waiting for the limiter isn't active time, so politeness never causes a timeout: on
  tenderned.nl the list page boots through 95 requests in ~91 s of waiting and ~3 s of active time.
- **Network log**: `s.network` keeps every XHR/fetch and JSON exchange (request headers without `cookie`, request
  body, response body up to 2 MB) with the index of the action that caused it, so an agent can replay a call over
  `PoliteClient` and read the API without a browser. `s.counts` counts requests by type, plus cache hits and aborts.

## Block detection: `detect_block(html, url=, status=, headers=)`
Returns a list of `BlockSignal(kind, strong, evidence)`, where `kind` is `captcha` or `login`. It's empty when there's
no sign of a block.
- **captcha:** reCAPTCHA, hCaptcha, Cloudflare Turnstile/challenge, Friendly Captcha, GeeTest, DataDome, or a form
  field named like `captcha`. *Strong* when the page itself is the challenge (challenge title, `cf-mitigated` header,
  or a 403/429/503 thin page).
- **login:** a password field, HTTP 401 or `WWW-Authenticate`. *Strong* on 401, or a password field on a login URL
  or a thin page. A login box next to a full tender list is *weak*.
- It only gathers evidence. Some portals show a captcha while the list and documents still load, so the agent must try
  the content first and only then report `Blocked` (T-015, T-022).

## Tests
`uv run pytest tests/net`. The browser and session tests run against local sites (`test_session.py` mimics a
portal: link to the list, a chunked app boot, load more, detail tabs) and are skipped when Chromium is missing:
install it with `uv run playwright install chromium` (the Docker image already has it).

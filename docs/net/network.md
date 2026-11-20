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
- One shared headless Chromium, a fresh context (no cookies) per render, at most 2 pages at once, 45 s timeout,
  waits for network idle.
- Every request the page makes waits for the same limiter. Images, media and fonts are aborted, so they're never sent.
- Known gap: redirect hops *inside* the browser are followed by Chromium without passing through the route hook, so
  only the first request of a redirect chain is spaced. Portal pages rarely chain more than one hop.

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
`uv run pytest tests/net`. The browser tests run against a local site and are skipped when Chromium is missing:
install it with `uv run playwright install chromium` (the Docker image already has it).

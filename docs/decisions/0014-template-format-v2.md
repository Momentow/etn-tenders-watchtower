# 0014 — Template format v2 and browser sessions

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-031 (epic T-030); implemented by T-032 (net) and T-033 (engine)
- **Amends:** [0003](0003-template-format.md) (adds `format_version: 2`; v1 keeps its meaning and keeps running)

## Context

The first live smoke run served 1 of 12 real portals, and that one served a "latest 20" widget as if it were every open
tender ([registry → Smoke runs](../portals/registry.md#smoke-runs)). A hand probe of tenderned.nl shows why v1 can't
describe real portals:

1. **The list isn't at the portal URL.** A person clicks *Alle aankondigingen* on `https://www.tenderned.nl/` and lands
   on `/aankondigingen/overzicht`. v1 has HTTP `follow`/`submit` steps only.
2. **The page is an app.** The list page makes 95 requests (93 s at 1 req/s) before its first list call. v1 renders
   once, with a 45 s timeout that also counts the time spent waiting for the rate limiter, so the render can never end.
3. **More tenders appear on a click** (*Laad meer*), and detail data sits behind tabs. v1 can't click.
4. **Behind the clicks there is a plain API**:
   `GET /papi/tenderned-rs-tns/v2/publicaties?page=N&size=50&publicatieDatumPreset=AF30`. Read directly, it's 50 calls
   for 2,474 notices and needs no browser. dtvp.de and evergabe.nrw.de have the same shape, but their API
   (`POST /Center/api/v2/project/search`) wants an `X-JWT` header that the search page issues to anonymous visitors once
   a session cookie exists. BOSA (publicprocurement.be) fetches an anonymous `client_credentials` token first.
5. **The list isn't "open tenders".** It mixes notice types: the tender a person opens first, 450459, is an
   early-termination notice. Serving it as open would be a wrong value.
6. **One tender can span several sibling rows** (marches-publics.info), and detail URLs carry volatile parts
   (`;jsessionid=…`, Wicket `?4`).

## Decision

### 1. Browser sessions (`watchtower.net`, T-032)

A **session** is one browser context held for a whole read or a whole agent exploration: cookies, storage and app state
carry from one step to the next.

```python
async with browser.session(budget=SessionBudget()) as s:      # BrowserPool.session(): one fresh context
    r = await s.act(Goto(url="https://www.tenderned.nl/"))    # -> ActionResult
    r = await s.act(Click(target=Target(role="link", name="Alle aankondigingen")))
    r = await s.act(LoadMore(target=Target(role="button", name="Laad meer"), max_clicks=3))
    s.url; await s.html(); await s.snapshot(); await s.cookies(); s.network   # all exchanges so far
```

- **Actions** are pydantic models in `watchtower.net.actions`, discriminated by `do`. The same models are embedded in v2
  templates (§2) and taken by agent tools (ADR 0015), so there is one definition:

  | `do` | Fields | Meaning |
  |---|---|---|
  | `goto` | `url` | Navigate; waits for the page to settle |
  | `click` | `target` | Click; waits for navigation or the network to settle |
  | `fill` | `target`, `value` | Type into an input |
  | `select` | `target`, `value` | Choose an option |
  | `press` | `target`, `key` | A key, e.g. `Enter` |
  | `wait_for` | one of `target`, `text`, `response` (URL regex) | Wait until it appears |
  | `load_more` | `target`, `max_clicks` (≤ 500), `until_count?` (css) | Click until the target is gone or disabled, nothing new appears, or a limit is reached |
  | `scroll` | `times` (≤ 50) | Scroll to the bottom, for infinite lists |
  | `tab` | `target` | Click a tab/accordion and wait for its content |

  `Target` is exactly one of `{role, name}`, `{text}`, `{css}`, `{xpath}`, plus an optional `nth`. Roles and visible
  names come first: they survive class-name churn. **There is no action that runs JavaScript** (templates stay data).
- **`ActionResult`**: `url`, `status` (main document, when it navigated), `network` (the `Exchange`s this action caused),
  `seconds_active`, `seconds_waiting` (rate limiter), `error`.
- **`Exchange`** (replaces nothing; `CapturedResponse` stays for `render`): `id`, `method`, `url`, `request_headers`
  (minus `cookie`), `request_body`, `status`, `content_type`, `resource_type`, `body` (text ≤ 2 MB), `truncated`,
  `action_index`. Kept for XHR/fetch and any JSON; documents and scripts are counted, not stored.
- **Time budget that doesn't punish politeness.** `SessionBudget(active_seconds=120, idle_ms=1500, max_seconds=1800)`.
  Time spent waiting in `HostRateLimiter.acquire` doesn't count as *active*. A page is settled when no request has
  been in flight (counting queued ones) for `idle_ms`. Playwright's own timeouts are set high; the session enforces the
  budget itself. So tenderned's 95-request boot takes ~95 s of waiting and a few seconds of active time, and ends.
- **Politeness unchanged.** Every request, sub-requests included, still waits for the shared limiter. Images, media
  and fonts are still aborted.
- **Static cache.** Successful `GET` scripts and stylesheets are cached in memory per host (LRU, 64 MB per process) and
  served from the cache on later sessions: a second boot of the same app costs a handful of requests, not 95.
- `BrowserPool.render(url)` stays, implemented as a one-`goto` session, so v1 templates and existing tools are unchanged.
- **Snapshot** (`await s.snapshot()`): a compact text view for the agent: title, URL, headings, landmarks, links and
  buttons with role and name, form fields, repeated blocks with counts, visible totals ("2.474 resultaten"). A few
  thousand characters at most.

### 2. Template format v2 (`watchtower.engine`, T-033)

`TemplateV2` in `watchtower.engine.template_v2`, `format_version: 2`. `engine.load_template(data)` picks v1 or v2 by
`format_version`; `run_template` runs either and returns the same `ReadResult`. Extraction (`Extract`, `on`, `formats`,
`pattern`, `map`, dropping unmapped values) is unchanged from 0003.

```
TemplateV2
├── format_version: 2
├── timezone, country?, software?, notes          as v1
├── entry: url                 where reading starts; any URL on the portal's site, may be deeper than the
│                              portal URL (https://www.tenderned.nl/ → …/aankondigingen/overzicht)
├── session?                   anonymous state a read needs, obtained fresh on every read
│   ├── via: http | browser
│   ├── steps: [Action] (browser) or [Request] (http)
│   └── capture: [{name, from: cookie|response_header|request_header|response_json, match (URL regex), key}]
├── list                       one of:
│   ├── kind: api      request (method, url, params, json/form body; may use {{session.name}}),
│   │                  items (jsonpath), total? (jsonpath), pagination (param | cursor | next_link | none)
│   └── kind: page     flow: [Action] after `entry`, items (css/xpath) with group?, total? (Extract on the page),
│                      pagination: none | next_link | param | click {target} | load_more {target, max_clicks} | scroll
├── detail?                    one of:
│   ├── kind: api      request with {id}/{field} placeholders, format json; page_url: the tender's public page
│                      ("…/overzicht/{id}"), which becomes the tender's `url`
│   └── kind: page     link (on the item) | url_template; via: http | browser;
│                      views: {name: [Action]}   e.g. {"documents": [{"do": "tab", "target": {…"Documenten"}}]}
├── fields                     contract fields; `on`: item | detail | detail.<view>
├── documents?                 as v1, plus `request` (an api call returning the file list) and `on: detail.<view>`
├── scope                      which records are open tenders:
│   ├── include: [Condition]   e.g. notice type in [AAO, AAN] (calls for competition)
│   ├── exclude: [Condition]   e.g. isVroegtijdigeBeeindiging == true
│   └── open_by_deadline: true drop records whose deadline has passed (default true)
└── urls                       normalisers for `url` and document links: drop_params [], drop_matrix [jsessionid],
                               drop_numeric_query (Wicket ?4)
```

- **`items.group`**: `{size: N}` (each N sibling elements form one tender) or `{starts_at: css}` (from each match to the
  next). The fields' item scope is then the whole group.
- **`Condition`**: `{source: Extract, in: [...]}`, `{source, matches: regex}` or `{source, equals: value}`; `source`
  runs on the list item. Dropped records are counted in `ReadResult.scope_dropped`, never stored.
- **`total`** is the portal's own count of what the list holds (`totalElements`, "2.474 resultaten"). It's recorded as
  `ReadResult.declared_total`. With `scope`, the API's own filter should do as much of the scoping as possible (query
  parameters on `list.request`), so the declared total and the delivered count can be compared (T-036).
- **Session values.** Requests can use `{{session.<name>}}` in headers, params and bodies. Values are captured on each
  read, kept in memory for that read only, never written to the template, the store or logs. A session is allowed
  only for what the portal gives anonymous visitors (a cookie, an `X-JWT` the search page issues, a `client_credentials`
  token its own JS requests). **Literal** `Authorization`/`Cookie` headers are still rejected (0003); a header that
  carries a `{{session.*}}` reference is allowed. Login pages, passwords and captcha answers stay out of scope.
- **Choosing a mode** (the agent's rule, enforced by nothing but cost): `kind: api` whenever a replay of the captured
  call over `PoliteClient` returns the same records as the page; a `session.via: browser` boot then costs one browser
  session per read and everything else is plain HTTP. `kind: page` only when there is no usable API.

### 3. Validation at load time

Everything 0003 checks, plus: actions and targets well-formed (exactly one target kind, limits in range);
`{{session.x}}` references name a declared capture; `detail.<view>` fields name a declared view; `group` fits a page
list; `scope` conditions compile; `entry` and every request URL are on the same registrable domain as the portal or a
host the session flow itself reached (an embedded API host).

### Worked examples

**tenderned.nl** (API, no session, no browser on regular reads):

```json
{"format_version": 2, "timezone": "Europe/Amsterdam", "country": "NL", "software": "tenderned",
 "entry": {"url": "https://www.tenderned.nl/aankondigingen/overzicht"},
 "list": {"kind": "api",
   "request": {"url": "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties", "params": {"size": "50"}},
   "items": "$.content[*]", "total": "$.totalElements",
   "pagination": {"kind": "param", "name": "page", "start": 0}},
 "detail": {"kind": "api", "request": {"url": "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties/{id}"},
            "page_url": "https://www.tenderned.nl/aankondigingen/overzicht/{id}"},
 "fields": {"id": {"by": "jsonpath", "expr": "$.publicatieId"},
            "title": {"by": "jsonpath", "expr": "$.aanbestedingNaam"}, "…": "…"},
 "documents": {"request": {"url": "https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties/{id}/documenten"},
               "links": {"by": "jsonpath", "expr": "$..links.download.href", "many": true}},
 "scope": {"exclude": [{"source": {"by": "jsonpath", "expr": "$.isVroegtijdigeBeeindiging"}, "equals": true}],
           "include": [{"source": {"by": "jsonpath", "expr": "$.publicatiecode.code"}, "in": ["…calls for competition…"]}]}}
```

**dtvp.de** (anonymous session from a browser boot, then API over HTTP):

```json
{"session": {"via": "browser",
   "steps": [{"do": "goto", "url": "https://www.dtvp.de/Center/common/project/search.do?method=showExtendedSearch&fromExternal=true"},
             {"do": "wait_for", "response": "/Center/api/v2/project/search"}],
   "capture": [{"name": "jwt", "from": "request_header", "match": "/Center/api/v2/project/search", "key": "x-jwt"},
               {"name": "cookies", "from": "cookie", "match": "dtvp\\.de", "key": "*"}]},
 "list": {"kind": "api", "request": {"method": "POST", "url": "https://www.dtvp.de/Center/api/v2/project/search",
            "headers": {"X-JWT": "{{session.jwt}}", "Cookie": "{{session.cookies}}"}, "json": {"…": "…"}}, "…": "…"}}
```

**A click-through HTML portal** (no API found):

```json
{"entry": {"url": "https://portal.example/"},
 "list": {"kind": "page", "flow": [{"do": "click", "target": {"role": "link", "name": "All tenders"}}],
          "items": {"by": "css", "expr": "article.tender"}, "total": {"by": "css", "expr": ".result-count", "pattern": "(\\d+)"},
          "pagination": {"kind": "load_more", "target": {"role": "button", "name": "Load more"}, "max_clicks": 100}},
 "detail": {"kind": "page", "link": {"by": "css", "expr": "h2 a", "attr": "href"}, "via": "browser",
            "views": {"documents": [{"do": "tab", "target": {"role": "tab", "name": "Documents"}}]}},
 "fields": {"submissionDeadline": {"by": "css", "expr": ".deadline", "on": "detail"}},
 "documents": {"on": "detail.documents", "links": {"by": "css", "expr": "a.download", "attr": "href", "many": true}}}
```

(Abbreviated: the real examples live in `watchtower/engine/examples/` once T-033 builds them, checked by tests.)

## Consequences

- **T-032 (net)**: `watchtower.net.actions` (Action, Target), `BrowserPool.session()` → `BrowserSession`,
  `SessionBudget`, `ActionResult`, `Exchange`, snapshot, static cache; `render()` rebuilt on a session. Tests run
  against a local site with a slow-boot page (many scripts), a load-more list and tabs.
- **T-033 (engine)**: `TemplateV2`, its JSON schema file, `load_template`, the v2 executor, `ReadResult.declared_total`
  and `scope_dropped`, examples, and a new mock portal kind (`app`: home → link → list with load-more and tabs, JSON API
  behind it, plus variants) built in `mockportals/`.
- **T-036 (validate)** gets the inputs it needs for completeness: `declared_total`, `scope_dropped`, pagination exhausted
  or not.
- The store keeps templates as JSON, so v2 needs no migration. Template diffs in the dashboard work unchanged.
- Cost: a `kind: api` read is as cheap as a v1 JSON read. A `kind: page` read with `via: browser` detail pages is the
  slowest path (one session per read, the static cache limits it); the agent avoids it when an API exists.
- Harder: the executor now has two formats. v1 stays frozen; new capabilities go to v2 only.

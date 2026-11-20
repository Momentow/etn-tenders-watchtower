# 0002 — Architecture, module boundaries and interfaces

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-002

## Context
Parallel sessions build different modules at the same time. They need fixed boundaries and interfaces to code against
before the code exists. The brief requires that regular reads run without a model call, and that the LLM learns, checks
and repairs how a portal is read.

## Decision

### Principle: static engine, agent produces templates
A deterministic **engine** reads every portal by executing a declarative **template** (data, not code). The LLM **agent**
only runs in two scenarios, **generate_template** (attach) and **investigate** (notice → repair). Its output is a
template or a diagnosis, never executable code. The engine validates every template before it is used.

### Process layout
One Python process (uvicorn, asyncio) serves the API, the dashboard and `/docs`, and runs the scheduler and workers in-process.

```
api/ ──► core/ (store, state machine, scheduler, jobs, pipeline) ──► engine/ ──► net/
dashboard/ ─┘        │                                   ▲             ▲
                     ├──► documents/ ────────────────────┼─────────────┤
                     └──► agent/ (scenarios) ──tools──► engine (dry run) + net
```

| Package | Owns | Must not |
|---|---|---|
| `watchtower/api` | HTTP routes and pydantic schemas exactly per the API contract; `/files/{id}` | Contain business logic or touch the DB directly, except through `core.store` |
| `watchtower/core` | Data models, `store` (SQLite), **portal state machine** (the only code that changes `state`/`reason`), scheduler, persisted job queue, read pipeline | Parse HTML or call the LLM |
| `watchtower/net` | `PoliteClient` (httpx) and `BrowserPool` (Playwright). **The only way to talk to portals**; enforces ≤ 1 req/s per portal host | — |
| `watchtower/engine` | Template schema, executor, normalizers (dates/time zones, CPV, enums), tender content hash | Write to the DB; call the LLM |
| `watchtower/validate` | Health checks on a read result versus history (the "Notice" signal) | Call the LLM |
| `watchtower/documents` | Streaming downloads, sha256 content-addressed storage, ZIP unpacking, the 250 MB rule | — |
| `watchtower/agent` | Agents SDK setup, tools, the two scenarios, prompts, HTML pruning. **The only package that calls a model** | Write to the DB or change portal state directly |
| `watchtower/dashboard` | Jinja2 + htmx pages at `/` | Write state |
| `mockportals/` | Local fake portals and their mutated variants, for tests and the demo | Ship as production logic |

### Interfaces (stable; changing them requires a new ADR)

```python
# net
class PoliteClient:
    async def get(self, url: str, **kw) -> httpx.Response: ...          # rate-limited per host
    def stream(self, method: str, url: str, **kw) -> AsyncContextManager[httpx.Response]: ...
class BrowserPool:
    async def render(self, url: str) -> RenderResult: ...              # html + captured JSON/XHR responses

# engine
async def run_template(template: Template, client: PoliteClient, browser: BrowserPool | None,
                       *, limit: int | None = None) -> ReadResult: ...
# ReadResult: tenders: list[TenderRecord] (normalized, contract-shaped, documents as DocumentRef(url, fileName?)),
#             errors: list[str], stats: ReadStats (pages, requests, durations)

# validate
def check(result: ReadResult, history: PortalHistory) -> Health: ...  # Health(ok: bool, problems: list[str])

# documents
async def fetch_documents(tender_key, refs: list[DocumentRef], client) -> list[StoredDocument]: ...

# agent (scenarios)
async def generate_template(portal_url: str) -> AttachOutcome: ...
# AttachOutcome = TemplateFound(template, notes) | Blocked(reason) | Unreadable(reason)
async def investigate(portal: PortalView, template: Template, result: ReadResult, health: Health,
                      *, trigger: Literal["failure", "audit"]) -> Diagnosis: ...
# Diagnosis = Healthy(notes)                     # only possible for trigger="audit"
#            | Transient(reason) | Changed(new_template, reason) | Blocked(reason) | Gone(reason)
#            | Unrepairable(reason)
```

The template format itself is defined in ADR 0003 (task T-008). Until then, `Template` is an opaque pydantic model.

### Portal lifecycle

| From → To | Trigger | Run recorded |
|---|---|---|
| (new) → `integrating` | `POST /portals` | — |
| `integrating` → `serving` | `generate_template` → `TemplateFound`, engine run passes validation | `read` ok (the first attach is a `read`) |
| `integrating` → `failed` | `Blocked` / `Unreadable`, or validation still failing after the attach budget | `read` failed |
| `serving` → `serving` | Scheduled or `refresh` read passes validation | `read` ok |
| `serving` → `healing` | Read fails (exception or failed validation) | `read` failed |
| `serving` → `serving` | Proactive audit → `Healthy` | `read` ok (audit shown on the dashboard) |
| `serving` → `healing` | Proactive audit finds a silent problem (`Changed`, missed tenders or wrong fields) | `read` failed, then a `repair` run |
| `healing` → `serving` | `investigate` → `Transient` (re-read ok) or `Changed` (new template validated and promoted) | `repair` ok |
| `healing` → `failed` | `Blocked` / `Gone` / `Unrepairable`, or the repair budget is exhausted | `repair` failed |
| `failed` → `integrating` | `POST /portals/{id}/refresh` on a failed portal starts a fresh attach | `read` |

- While `healing`, the API keeps serving the last correct tenders. Tenders from failed reads are never stored.
- Templates are versioned. A new version is promoted only after it passes validation; old versions are kept for audit and the dashboard.
- `reason` is always a plain-language sentence set by the state machine from the outcome.
- `DELETE` marks the portal deleted at once. Workers check that flag before each request, so calls stop within a minute.

### Read pipeline (no model call)
`run_template` → `validate.check` → upsert tenders (content hash changed ⇒ `lastChangedAt=now`; deadline passed or no longer
listed ⇒ `closed`) → enqueue download jobs for new or changed documents → record the run → state machine.

### Health-check modes
Set by `WATCHTOWER_HEALTHCHECK_MODE`; the scheduler in `core` owns both triggers.

| Mode | Trigger | What happens |
|---|---|---|
| `reactive` | A read fails: an exception while scraping, or `validate.check` fails | `investigate(trigger="failure")`: exact issue report → repair/heal the template or fail with a reason |
| `proactive` (default) | Everything in `reactive`, **plus** every `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` (default 24) per `serving` portal | `investigate(trigger="audit")`: the agent compares engine output with the live portal (missed tenders, new pagination, fields now shown but not extracted, wrong values) → `Healthy` or repair |

- Audits catch **silent** breakage that deterministic validators can't see. They cost one bounded scenario per portal per interval, which is included in the cost per portal (T-099).
- The contract only knows `read` and `repair` runs, so an audit is recorded as a `read` run. Its audit details are internal and shown on the dashboard.
- Audits are staggered across portals, never run while another job for the portal is active, and are skipped for `healing`/`failed` portals.

### Agent toolkit: deterministic first
Rule: **anything that can be computed deterministically is a tool, not model reasoning.** The model makes the judgement
calls (which block is the tender list, which mapping is right). Tools return compact, structured summaries, never raw
pages, which keeps turns and tokens low and results stable. All tools go through `net` (politeness) and are pure
functions of their inputs plus fetched content. The full list and its acceptance criteria are in T-014 and T-022. Categories:

- **Discovery:** pruned DOM outline; captured JSON/XHR endpoints with key summaries; repeating-block (list) detection;
  pagination detection; link classification (detail pages vs documents, with multilingual keywords); summary-page →
  real-tender-page resolution.
- **Recognition:** portal-software fingerprint (URL path, footer, `meta generator`) → **reuse of an existing template
  from a portal running the same software**; field-label mapping from a multilingual synonym dictionary; date-format and
  time-zone probe; login/captcha detection, including "captcha shown but documents still download".
- **Verification:** selector tester (CSS/XPath/JSONPath/regex on a sample); `try_template` dry run with field-coverage
  stats and validation report; document download probe (content type, size, filename, HTML-instead-of-file detection).
- **Repair:** structural diff of the page against the snapshot from the last good read; output diff between template
  versions.

The engine stores a structural snapshot of the list page with every successful read, to feed the repair diff.

### Scheduling and politeness
- Persisted job table: kinds `attach`, `read`, `investigate`, `download`. Workers are asyncio tasks, and unfinished jobs resume after a restart.
- At most one active read/attach/investigate per portal. `refresh` returns the running read's `runId`.
- Rate limiting is per **portal host**, inside `net`, shared by engine, documents and agent tools. Nothing can bypass it.
- Agent scenarios have budgets (max turns, max tokens, wall time). HTML is pruned before it reaches the model.

## Consequences
- Modules can be built in parallel against these signatures, with stubs where a dependency isn't merged yet.
- Notice is cheap and deterministic. The model is called only on attach, on a failed read, and (in proactive mode) once per audit interval.
- Cost per portal ≈ one attach scenario + one audit per interval + occasional investigations. Template reuse by software fingerprint lowers the attach cost. Metering hooks live in `agent` only (T-099).

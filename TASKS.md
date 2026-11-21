# Tasks

Shared board for every human and Claude session. Protocol: [CLAUDE.md → TASKS.md protocol](CLAUDE.md#tasksmd-protocol).

**Statuses:** `todo` · `in-progress` · `review` (PR open) · `done` · `blocked`
**Claim:** set Status, Owner and Branch in a tiny commit pushed straight to `master`. Only claim a task once all its dependencies are `done`.
**Areas:** `core` · `api` · `net` · `engine` · `notice` · `repair` · `agent` · `documents` · `dashboard` · `docs-site` · `infra` · `test-portals` · `cost`
**Interfaces:** every module codes against [ADR 0002](docs/decisions/0002-architecture.md). Stub a dependency that isn't merged yet; never wait on another session's branch.

| ID | Title | Area | Status | Owner | Branch | Depends on | Refs |
|---|---|---|---|---|---|---|---|
| T-000 | Collaboration protocol & docs skeleton | docs-site | done | Momentow | docs/T-000-collab-protocol | — | [CLAUDE.md](CLAUDE.md) |
| T-001 | Build test-portal registry | test-portals | done | OrangeCanvas705 | docs/T-001-test-portal-registry | T-000 | [registry](docs/portals/registry.md) |
| T-002 | Tech stack & architecture ADRs | core | done | Momentow | docs/T-000-collab-protocol | T-000 | [0001](docs/decisions/0001-tech-stack.md), [0002](docs/decisions/0002-architecture.md) |
| T-003 | Project skeleton, Dockerfile, checks | infra | done | Momentow | chore/T-003-project-skeleton | T-002 | [0001](docs/decisions/0001-tech-stack.md) |
| T-004 | SQLite store & data models | core | done | Lavrtajx | feat/T-004-sqlite-store | T-003 | [0002](docs/decisions/0002-architecture.md), [api-contract](docs/spec/api-contract.md) |
| T-005 | HTTP API per contract | api | done | Lavrtajx | feat/T-005-http-api | T-004 | [api-contract](docs/spec/api-contract.md) |
| T-006 | Scheduler, job queue & state machine | core | done | Lavrtajx | feat/T-006-scheduler-state-machine | T-004 | [0002 lifecycle](docs/decisions/0002-architecture.md#portal-lifecycle) |
| T-007 | Polite network layer (`PoliteClient`, `BrowserPool`) | net | done | Lavrtajx | feat/T-007-polite-network-layer | T-003 | [0002](docs/decisions/0002-architecture.md) |
| T-008 | Template format (ADR 0003 + pydantic schema) | engine | done | Lavrtajx | feat/T-008-template-format | T-003 | [0002](docs/decisions/0002-architecture.md) |
| T-009 | Engine executor (`run_template`) | engine | done | OrangeCanvas705 | feat/T-009-engine-executor | T-007, T-008 | [0002](docs/decisions/0002-architecture.md) |
| T-010 | Normalizers & tender content hash | engine | done | Lavrtajx | feat/T-010-normalizers | T-003 | [api-contract → Tender](docs/spec/api-contract.md#tender) |
| T-011 | Validators (`validate.check`) | notice | done | OrangeCanvas705 | feat/T-011-validators | T-009 | [0002](docs/decisions/0002-architecture.md) |
| T-012 | Document downloader & `/files` store | documents | done | Lavrtajx | feat/T-012-document-downloader | T-004, T-007 | [api-contract → Tender](docs/spec/api-contract.md#tender) |
| T-013 | Mock portals + mutated variants | test-portals | done | OrangeCanvas705 | feat/T-013-mock-portals | T-003 | [requirements](docs/spec/requirements.md#finding-test-portals) |
| T-014 | Agent runtime & tools | agent | done | Momentow | feat/T-014-agent-runtime | T-007, T-009 | [0001](docs/decisions/0001-tech-stack.md), [0002](docs/decisions/0002-architecture.md) |
| T-015 | Scenario `generate_template` (Attach) | agent | done | Momentow | feat/T-015-generate-template | T-013, T-014 | [0002](docs/decisions/0002-architecture.md) |
| T-016 | Scenario `investigate` (Notice → Repair) | repair | done | Lavrtajx | feat/T-016-investigate | T-011, T-013, T-014 | [0002 lifecycle](docs/decisions/0002-architecture.md#portal-lifecycle) |
| T-017 | Read pipeline wiring (attach / read / heal flows) | core | done | OrangeCanvas705 | feat/T-017-read-pipeline | T-006, T-009, T-010, T-011, T-012 | [0002 read pipeline](docs/decisions/0002-architecture.md#read-pipeline-no-model-call) |
| T-018 | Dashboard | dashboard | done | Lavrtajx | feat/T-018-dashboard | T-005 | [requirements](docs/spec/requirements.md) |
| T-019 | Docs site at `/docs` | docs-site | done | OrangeCanvas705 | docs/T-019-docs-site | T-003 | [0001](docs/decisions/0001-tech-stack.md) |
| T-022 | Deterministic agent toolkit | agent | done | Momentow | feat/T-022-agent-toolkit | T-007, T-009, T-010 | [0002 toolkit](docs/decisions/0002-architecture.md#agent-toolkit-deterministic-first) |
| T-023 | Health-check modes (reactive / proactive audit) | notice | done | Lavrtajx | feat/T-023-health-check-modes | T-006, T-016, T-017 | [0002 health checks](docs/decisions/0002-architecture.md#health-check-modes) |
| T-020 | End-to-end tests & real-portal smoke run | test-portals | done | Momentow | feat/T-020-e2e-smoke | T-001, T-015, T-016, T-017, T-023 | [requirements → Judging](docs/spec/requirements.md#judging) |
| T-030 | **Epic: Engine v2 & Agent v2** (browser-first actions, API capture, tiered models) | engine, agent | todo | | | T-020 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-031 | ADRs: engine v2 template format + agent v2 model tiers | engine, agent | done | Momentow | docs/T-031-engine-v2-adrs | T-030 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-032 | Browser sessions: actions, long polite boot, network capture | net | done | Momentow | feat/T-032-browser-sessions | T-031 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-033 | Template format v2 + executor (actions, entry shortcut, API mode, filters, row groups) | engine | done | Lavrtajx | feat/T-033-template-v2-executor | T-031, T-032 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-034 | Agent v2 explorer: browser tools, recorded flow → API, v2 templates | agent | todo | | | T-033 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-035 | Agent v2 tiers: strong planner, light workers, budgets per tier | agent | done | OrangeCanvas705 | feat/T-035-model-tiers | T-031 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-036 | Completeness: never `serving` on a partial read | validate, notice | in-progress | Lavrtajx | feat/T-036-completeness | T-033 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-039 | Truthful blocks: `Blocked` only on evidence; constant gateway 403 is a block (split from T-036) | agent, notice | in-progress | OrangeCanvas705 | feat/T-039-truthful-blocks | T-031 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-037 | Investigate/repair and audits on v2 | repair, notice | todo | | | T-034, T-035, T-036, T-039 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-038 | v2 live re-run: the 12-portal demo scenario, then `unseen` | test-portals | todo | | | T-037 | [T-030 section](#t-030--engine-v2--agent-v2-epic) |
| T-021 | README: how it works, env vars, running it (split: demo script → T-040) | docs-site | done | Momentow | docs/T-021-readme | T-020 | [requirements → Hand-in](docs/spec/requirements.md#hand-in) |
| T-040 | Demo video script (split from T-021) | docs-site | todo | | | T-021, T-038 | [requirements → Hand-in](docs/spec/requirements.md#hand-in) |
| T-099 | LLM usage metering & cost per portal (last MVP task) | cost | todo | | | T-020, T-035 | [model-usage](docs/cost/model-usage.md) |

**Critical path:** T-003 → T-007/T-008 → T-009 → T-014 → T-015/T-016 → T-017 → T-023 → T-020 → **T-031 → T-032 → T-033 → T-034/T-035/T-036 → T-037 → T-038** → T-099.
**T-022 runs alongside T-014–T-016:** the scenarios adopt toolkit tools as they're merged; they don't wait for all of them.
**Can start in parallel right after T-003:** T-004, T-007, T-008, T-010, T-013, T-019. T-001 can start now (no code needed).

---

### T-000 — Collaboration protocol & docs skeleton
**Goal:** Every session follows one protocol for git, tasks, docs and contracts.
**Acceptance:**
- [x] `CLAUDE.md` protocol · [x] `TASKS.md` board · [x] `docs/` skeleton
**Refs:** [CLAUDE.md](CLAUDE.md)

### T-001 — Build test-portal registry
**Goal:** Our own test set, as the brief asks for.
**Acceptance:**
- [x] Each portal: a public tender list, ≥ 5 open tenders with documents downloadable without login
- [x] ≥ 4 countries, ≥ 3 portal software packages
- [x] Mix of `ssr`, `js-api` and `multi-step`; 1–2 `login`/`captcha` portals
- [x] A few marked `unseen` (held back for the final test)
**Refs:** [registry](docs/portals/registry.md), [requirements](docs/spec/requirements.md#finding-test-portals)
**Notes:** Machine-readable set in `testportals/` (portals.json, TED fixtures, pinned list, `ted_portals.py`). Per-tender access
varies on vmp-satellite and PLACE (4–6 of 6 sampled tenders had anonymous documents). marches-publics.info, s2c.mercell.com and
acquistinretepa.it were not fully verified, and the publicprocurement.be document download is not yet confirmed (we don't submit captchas); see the registry.

### T-002 — Tech stack & architecture ADRs
**Acceptance:**
- [x] ADR 0001 and ADR 0002 accepted
- [x] CLAUDE.md stack and architecture sections filled in
- [x] MVP tasks on this board
**Refs:** [0001](docs/decisions/0001-tech-stack.md), [0002](docs/decisions/0002-architecture.md)

### T-003 — Project skeleton, Dockerfile, checks
**Goal:** An empty but runnable service that everyone builds on.
**Acceptance:**
- [x] `pyproject.toml` + `uv.lock` with the ADR 0001 libraries pinned; package `watchtower/` with the ADR 0002 sub-packages (empty modules)
- [x] FastAPI app factory; `GET /` returns a placeholder page; app starts with `uv run …`
- [x] `ruff` and `pytest` configured, with one passing smoke test
- [x] `Dockerfile` on the Playwright Python base image; `docker build .` works on linux/amd64; the container serves `:8080`
- [x] CLAUDE.md "Commands" section filled in with the real commands
**Refs:** [0001](docs/decisions/0001-tech-stack.md)

### T-004 — SQLite store & data models
**Goal:** `core.store`, the only DB access layer.
**Acceptance:**
- [ ] Tables: portals, runs, template_versions, tenders, documents, jobs. Migrations applied at startup (WAL mode)
- [ ] Async repository functions used by the API, the scheduler and the pipeline
- [ ] Portal URL normalisation so the same address returns the same id
**Refs:** [0002](docs/decisions/0002-architecture.md), [api-contract](docs/spec/api-contract.md)

### T-005 — HTTP API per contract
**Acceptance:**
- [ ] Every endpoint, status code and field exactly as in the contract; times with offsets; unknown id → 404
- [ ] `POST /portals` returns 201 at once and enqueues an attach; `refresh` returns 202 with the running read's `runId` if one is running; `changedSince` filter works
- [ ] Contract tests for every endpoint
**Refs:** [api-contract](docs/spec/api-contract.md)

### T-006 — Scheduler, job queue & state machine
**Acceptance:**
- [ ] Persisted jobs (`attach`, `read`, `investigate`, `download`) resume after a restart; asyncio workers
- [ ] At most one active attach/read/investigate per portal; periodic reads of `serving` portals
- [ ] The state machine is the only writer of `state`/`reason`, and implements the ADR 0002 lifecycle table
- [ ] Deleted portals: no requests within a minute
- [ ] Reads `WATCHTOWER_HEALTHCHECK_MODE` / `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` from the environment and exposes due-audit scheduling for T-023 (staggered, one job per portal at a time)
**Refs:** [0002 lifecycle](docs/decisions/0002-architecture.md#portal-lifecycle)

### T-007 — Polite network layer
**Acceptance:**
- [ ] `PoliteClient.get/stream`: ≤ 1 req/s per host (tested), timeouts, retries with backoff, honest User-Agent
- [ ] `BrowserPool.render`: a shared Chromium that returns HTML plus captured JSON/XHR responses, going through the same limiter
- [ ] Login/captcha detection helpers (used for `Blocked`)
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-008 — Template format
**Goal:** The contract between the agent and the engine.
**Acceptance:**
- [ ] ADR 0003 accepted, covering:
  - the list source (HTML / JSON API / multi-step)
  - pagination
  - field extractors (CSS / XPath / JSONPath / regex + constants)
  - detail-page follow
  - document links and multi-step downloads
  - time zone and date formats
  - status signals (how the portal marks a tender `withdrawn`/cancelled)
  - the browser-needed flag
- [ ] Pydantic `Template` model + JSON schema (the agent uses it for structured output)
- [ ] Example templates for every mock portal kind
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-009 — Engine executor
**Acceptance:**
- [ ] `run_template` returns a `ReadResult` exactly per ADR 0002, with no DB access and no LLM call
- [ ] Works on the mock portals with their example templates; `limit` supports dry runs
- [ ] Returns a structural snapshot of the list page (DOM outline or JSON key shape) to be stored with each good read (used by the repair diff tool)
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-010 — Normalizers & content hash
**Acceptance:**
- [ ] Local date/time + portal time zone → ISO 8601 with offset; publication date as a date
- [ ] CPV check digit stripped; procedureType and contractNature mapped (unknown → leave out); ISO country codes
- [ ] A content hash that's stable across reads (drives `lastChangedAt`)
- [ ] Unit tests covering several locales (DE, FR, ES, NL, PL formats)
**Refs:** [api-contract → Tender](docs/spec/api-contract.md#tender)
**Notes:** T-009 calls normalisation through one entry point, `watchtower/engine/basic_normalize.py::normalize_fields(raw, template)` (a minimal placeholder). Replace that module's body (or point the executor's single call at the new module); see [executor docs](docs/engine/executor.md#normalisation-placeholder-until-t-010).

### T-011 — Validators
**Acceptance:**
- [ ] `check(result, history)` flags: fetch/parse errors, zero tenders, a sharp drop versus recent reads, required fields newly null, invalid dates, duplicate ids
- [ ] Problems written as plain sentences (they feed `reason` and the dashboard)
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-012 — Document downloader
**Acceptance:**
- [ ] Streams to disk with sha256; content-addressed; never fully in RAM
- [ ] Unpacks ZIPs (nested too) on disk; skips files over 250 MB (checked from the headers or while streaming)
- [ ] `GET /files/{id}` serves the bytes; `documents[]` entries match the contract
**Refs:** [api-contract → Tender](docs/spec/api-contract.md#tender)

### T-013 — Mock portals + mutated variants
**Acceptance:**
- [x] Locally served portals: SSR list + detail pages, a JS app with a JSON API, a multi-step download, a captcha/login variant
- [x] Mutated variants (changed markup, renamed JSON keys, moved pagination) for Notice and Repair tests
- [x] A "silent" variant: still parses, but tenders are missed or a field moves (for proactive audit tests)
- [x] Usable from pytest and runnable for the demo
**Refs:** [requirements](docs/spec/requirements.md#finding-test-portals), [mock portals](docs/test-portals/mock-portals.md)
**Notes:** `uv run python -m mockportals` (:8081). Matches the T-008 example templates; `expected_tenders()` is the ground
truth for engine/E2E tests. Use `selectolax.lexbor` (the old `selectolax.parser` backend raises ImportError in 1.x).

### T-014 — Agent runtime & tools
**Acceptance:**
- [x] Agents SDK setup reading `OPENAI_API_KEY` / `WATCHTOWER_MODEL`; the only place model calls happen
- [x] Core tools: `fetch` (pruned outline), `render` (captured JSON/XHR summary), `try_template` (engine dry run), `submit_template`, `report_blocked`, `report_transient`
- [x] A tool registry that T-022 extends by adding tools, without editing scenario code
- [x] HTML pruning before content reaches the model; per-scenario budgets (turns, tokens, wall time)
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-015 — Scenario `generate_template`
**Acceptance:**
- [x] Returns an `AttachOutcome`; handles summary-page links (finds the real tender list); detects login/captcha → `Blocked`
- [x] Doesn't report `Blocked` when a captcha is shown but the list and documents still download without it (try first, then decide)
- [x] Produces a validated template for every mock portal kind
**Refs:** [0002](docs/decisions/0002-architecture.md)
**Notes (2026-11-20):** live runs on all 7 mock kinds match (5 `TemplateFound`, `login`/`robot` → `Blocked`); results in `docs/agent/attach.md`. PR open.
**Notes (handover, 2026-11-21):** released by OrangeCanvas705; Momentow finishes it. The work so far is on the pushed branch
`feat/T-015-generate-template` (4 commits on top of master, rebased; no PR yet). Continue on that branch rather than
starting over: `git fetch && git switch feat/T-015-generate-template && git rebase origin/master`.
- **Done on the branch:**
  - `watchtower/agent/attach.py`: `ATTACH_PROMPT` (find the real list first, `detail.resolve` for summary pages, a
    `status` rule when the portal lists everything, try first then decide on captchas), `ATTACH_BUDGET` (30 calls / 400k
    tokens / 900 s), `run_attach(url, client, browser, *, budget, model) -> AttachRun(outcome, run)` and
    `make_generate_template(client, browser)`, which returns the `GenerateTemplate` callable the pipeline takes.
  - Outcome mapping: accepted template → `TemplateFound`; `report_blocked` → `Blocked`, **except** when a template in
    `ctx.tried` already had a GOOD dry run (documents included) → `TemplateFound` with that template (deterministic
    try-first override; notes say the model reported a block); `report_transient` → `Unreadable("the portal could not be
    read right now (…)")`; `report_unreadable` → `Unreadable`; budget/no closing call → `Unreadable` naming the limit;
    missing `OPENAI_API_KEY` → `Unreadable("the agent is not configured (…)")`, never a crash.
  - `app.py` (own commit): `pipeline.configure(..., scenarios=Scenarios(generate_template=make_generate_template(http,
    browser)))`; `investigate` stays `stubs.investigate` until T-016 swaps it in the same call.
  - `watchtower/agent/probe.py` now runs `run_attach` (the draft prompt is gone) and prints the raw run and the mapped outcome.
  - Tests: `tests/agent/test_attach.py` (8, scripted model, mock portals, incl. a full `attach` job through the pipeline
    → `serving`, 20 tenders). Full suite 462 passed, ruff clean, zensical build clean.
  - Docs: new `docs/agent/attach.md` (+ nav, docs home); `docs/core/pipeline.md` and `docs/agent/runtime.md` updated.
- **Acceptance status:**
  - Item 1: `AttachOutcome` mapping is done and tested. Summary-page handling and login detection rely on the prompt and
    the T-014 tools (`try_template`'s document check flags login/captcha pages); **not yet seen working with a real model**.
  - Item 2: the deterministic override is tested with the `ssr` template (scripted). It was **not** tested on the
    `captcha` mock, because `captcha_bypassable` needs `render: true` (a real browser on a real socket:
    `mockportals.testing.serve()` + `BrowserPool`), which the in-process ASGI tests can't do.
  - Item 3: **open. No live run was made**: there was no `OPENAI_API_KEY` in OrangeCanvas705's environment.
- **Next steps:**
  1. Live runs with a key in `.env`: `uv run --env-file .env python -m watchtower.agent.probe --mock <kind>` for
     `ssr js multistep form captcha` (expect `TemplateFound`) and `login robot` (expect `Blocked`). Up to 400k tokens each.
  2. Tune `ATTACH_PROMPT` from what fails. If something can be computed, make it a T-022 tool instead of more prompt.
  3. Optional: an opt-in browser test of the `captcha` kind with `serve()` + `BrowserPool` (Chromium is in the image).
  4. Tick the boxes, open the PR (title `T-015: Scenario generate_template (Attach)`), set this task to `review`.
- **Open points:** `Transient` during attach maps to `Unreadable`, so the portal goes `failed` and isn't retried on its
  own (raising instead would use the scheduler's retries, but the final reason would read "the attach crashed").
  Template reuse by software fingerprint is T-022 (`find_template_for_software`), not wired here.

### T-016 — Scenario `investigate`
**Acceptance:**
- [ ] Returns a `Diagnosis`; repairs every mutated mock variant (`Changed` with a validated template)
- [ ] Separates transient errors from blocked, gone and unrepairable portals
- [ ] Supports `trigger="failure"` (exact issue report) and `trigger="audit"` (compares engine output with the live portal → `Healthy` or a repair)
**Refs:** [0002 lifecycle](docs/decisions/0002-architecture.md#portal-lifecycle)

### T-017 — Read pipeline wiring
**Acceptance:**
- [ ] Attach, read and heal flows per the ADR 0002 lifecycle, with `run` records for each
- [ ] Tender upsert: `lastChangedAt`; `closed` when the deadline has passed or the tender is no longer listed
- [ ] While healing, the last correct tenders are still served; template versions are promoted only after validation
- [ ] Calls agent scenarios through the ADR 0002 interfaces (stubbed until T-015/T-016 merge)
**Refs:** [0002 read pipeline](docs/decisions/0002-architecture.md#read-pipeline-no-model-call)

### T-018 — Dashboard
**Acceptance:**
- [ ] `/`: every portal with state, reason, tender count and last success; live refresh via htmx
- [ ] Portal detail: run timeline (reads, repairs, diagnoses), template versions, tenders
**Refs:** [requirements](docs/spec/requirements.md)

### T-019 — Docs site at `/docs`
**Acceptance:**
- [ ] `mkdocs.yml` covering all of `docs/`; built with Zensical during `docker build` (fall back to pinned mkdocs-material if Zensical fails)
- [ ] Served statically at `/docs` by the app; clean, minimal look with search
**Refs:** [0001](docs/decisions/0001-tech-stack.md)

### T-022 — Deterministic agent toolkit
**Goal:** Shorter, faster, more stable agent runs: the model judges, tools compute. Each tool is registered in the T-014
registry, returns a compact structured summary, goes through `net`, and has unit tests on the mock portals.
**Acceptance:**
- [x] Discovery:
  - `find_repeating_blocks` (candidate tender-list containers + item count + sample)
  - `find_pagination`
  - `classify_links` (detail pages vs documents; multilingual keywords such as Vergabeunterlagen, DCE, pliegos, dokumentacja)
  - `resolve_tender_page` (summary/notice page → the real tender page)
  - `summarize_json` (keys, types, array lengths of a captured endpoint)
- [x] Recognition:
  - `fingerprint_software` (URL path, footer, `meta generator`) + `find_template_for_software` (reuse a template from another portal with the same fingerprint)
  - `map_field_labels` (multilingual synonym dictionary → contract fields)
  - `probe_dates` (format + time zone from sample strings and the country)
  - `detect_block` (login/captcha; also checks whether the documents download anyway)
- [x] Verification:
  - `test_selector` (CSS/XPath/JSONPath/regex on a sample)
  - `try_template` with field-coverage stats
  - `probe_document` (content type, size, filename, HTML-instead-of-file)
- [x] Repair:
  - `diff_structure` (live page vs the last good snapshot)
  - `diff_outputs` (template version A vs B)
**Refs:** [0002 toolkit](docs/decisions/0002-architecture.md#agent-toolkit-deterministic-first)

### T-023 — Health-check modes
**Acceptance:**
- [ ] `reactive`: an exception or failed validation during a read → `healing` → `investigate(trigger="failure")` → repair or `failed` with an exact reason
- [ ] `proactive` (default): `reactive` behaviour, **plus** a staggered audit of every `serving` portal every `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` (default 24) via `investigate(trigger="audit")`
- [ ] Audits recorded as `read` runs; audit details shown on the dashboard; a silent problem the audit finds leads to `healing` and a `repair` run
- [ ] Tests: both modes on mock portals, including a "silent" mutation (markup still parses, but tenders are missed) that only the audit catches
**Refs:** [0002 health checks](docs/decisions/0002-architecture.md#health-check-modes)

### T-020 — End-to-end tests & real-portal smoke run
**Acceptance:**
- [ ] E2E on mock portals: attach → serving; mutate → healing → serving; captcha → failed with reason
- [ ] Smoke run against the registry's `dev` portals; results noted in the registry
- [ ] ~~Final run against the `unseen` portals~~ moved to T-038 (needs engine v2)
**Refs:** [requirements → Judging](docs/spec/requirements.md#judging)

### T-030 — Engine v2 & Agent v2 (epic)

**Why.** The first live smoke run (2026-11-21, [registry → Smoke runs](docs/portals/registry.md#smoke-runs)) served
1 of 12 real portals, and that one was wrong too: evergabe.nrw.de served the homepage's "latest 20" widget as if it were
every open tender. Probing tenderned.nl by hand shows what real portals need and what v1 can't express:

| Human path on tenderned.nl | What it really is | v1 |
|---|---|---|
| Home → click **Alle aankondigingen** | `/aankondigingen/overzicht`; the portal URL isn't the list URL | ✗ only HTTP `follow`/`submit` steps |
| The list renders | Angular app: 95 requests (93 s at 1 req/s) before the first list call | ✗ one render, 45 s timeout |
| **Laad meer** | `GET /papi/tenderned-rs-tns/v2/publicaties?page=N&size=50` | ✗ no clicks; the API was never captured |
| Click a tender → `/aankondigingen/overzicht/450459`, tabs per category | SPA route + detail and `…/documenten` JSON | ✗ no tabs, no clicks |
| "All open" | The list mixes notice types (450459 is an early-termination notice); 2,474 in 30 days, 147,334 in total | ✗ no filters: would serve awards as open tenders |

Once the API is known, the regular read is 50 JSON calls a minute and no browser. dtvp.de and evergabe.nrw.de need the same
pattern with a session (`POST /Center/api/v2/project/search` with an `X-JWT` the search page issues). The other failures
were: one login redirect ending the attach (dtvp.de, PLACE: both download anonymously), lists not found (achatpublic.com,
contractaciopublica.cat) and one tender spread over sibling rows (marches-publics.info).

**Goal.** Watchtower learns portals the way a person explores them (open, click, load more, open a tender, switch tabs),
records what the page fetches, and keeps the cheapest **declarative** template that reproduces it: plain API calls when
the flow reveals an API, browser actions when it doesn't. Strong models explore and judge; light models do repetitive
checks under a strong model's supervision.

**Unchanged rules (CLAUDE.md):** templates stay data, never code; regular reads make no model call; ≤ 1 req/s per portal
for every request, browser sub-requests included; no logins or captcha solving; a wrong value is worse than a missing one.

**Design outline (to be fixed by T-031's ADRs):**

1. **Browser-first sessions (net, T-032).** A portal read can hold one browser context for the whole read (cookies,
   tokens, SPA state). Actions: `goto`, `click` (role/text/selector), `fill`, `select`, `press`, `wait_for`
   (selector/text/network response), `load_more` (click until gone or a limit), `scroll`, `tab`. The time budget counts
   *our* waiting separately from the limiter's spacing, so a 95-request boot isn't a timeout. Every XHR/fetch is recorded
   (method, URL, body, status, content type, size) with the action that caused it. Scripts/CSS are cached per portal
   across reads to cut the boot cost.
2. **Template format v2 (engine, T-033).** `format_version: 2`; v1 templates keep running.
   - `entry`: where reading starts, which may be a shortcut deeper than the portal URL (`https://www.tenderned.nl/` →
     `/aankondigingen/overzicht`).
   - `flow`: declarative action steps (above) for list, pagination and detail, including tab switches per field group.
   - `api` mode: list/detail/documents read straight from JSON endpoints, with an optional `bootstrap` (a session
     cookie, a token from a `client_credentials` call, a header captured during a browser boot) refreshed when it expires.
   - `scope`: which records count as open tenders: notice-type allow-list, `deadline > now`, status values; declared
     total (`totalElements`, "2,474 results") recorded for completeness checks.
   - `items.group`: one tender spread over N sibling rows, or from one marker element to the next.
   - URL normalisers (strip `;jsessionid`, Wicket `?N`).
3. **Agent v2 explorer (agent, T-034).** Browser tools: open, accessibility snapshot (roles, names, links, buttons,
   counts of repeated blocks), click/fill/load-more, network log since the last action, "replay this XHR without the
   browser", "turn this flow into a template and dry-run it". Prefers API mode when the replay matches what the page shows.
4. **Model tiers (agent, T-035).** `WATCHTOWER_MODEL_STRONG` (explore, plan, judge, repair) and `WATCHTOWER_MODEL_LIGHT`
   (map labels to fields, check one tender against its page, classify notice types, verify a selector on samples),
   with budgets per tier. The strong model dispatches light tasks as tools and reviews their results; all calls stay
   in `agent` so T-099 can meter them per tier.
5. **Completeness and truthful blocks (validate/notice, T-036).**
   - Not `serving` when the read is partial: fewer tenders than the portal's declared total in scope; a "latest N"
     widget used as the list (no pagination while the page offers more); a list whose notice types aren't filtered.
   - A block is reported only after: at least 2 tenders tried, the list page's session reused, and any
     anonymous-download route tried. A constant 403 from a gateway is a block, not "temporary".
6. **Repair and audits on v2 (T-037).** Investigate replays the recorded flow, diffs network calls and snapshots against
   the last good read, and repairs a step, a selector or an endpoint. Proactive audits also re-check the declared total.

**Acceptance (epic, measured by T-038 with `testportals/smoke.py dev blocked unverified`):**
- [ ] tenderned.nl, publicprocurement.be, evergabe.nrw.de, dtvp.de and marches-publics.gouv.fr `serving` with **every
  open tender in scope** (count within 5 % of the portal's own total for open calls for competition), documents
  downloaded for tenders that offer them anonymously
- [ ] No `dev` portal reported `failed` with a login/captcha reason when its documents download anonymously
- [ ] The 3 `blocked` portals `failed`, with a block reason that is true
- [ ] No portal `serving` on a partial read (the evergabe "latest 20" case must end `failed` or keep integrating)
- [ ] Regular reads: zero model calls; API-mode reads use no browser
- [ ] Mock portals: all of `tests/` green, plus a new mock portal kind for click/load-more/tabs (T-033)
- [ ] Model cost per attach written down per tier (feeds T-099)

### T-031 — ADRs: engine v2 template format + agent v2 model tiers
**Acceptance:**
- [ ] ADR "Template format v2" (supersedes the relevant parts of 0003): entry, flow actions, api mode + bootstrap, scope,
  row groups, normalisers, v1 compatibility; worked examples for tenderned.nl (api), dtvp.de (session + api) and a
  click/load-more HTML portal
- [ ] ADR "Agent v2: explorer and model tiers" (amends 0010/0012): tools, tier roles, budgets, env vars
  (`WATCHTOWER_MODEL_STRONG`, `WATCHTOWER_MODEL_LIGHT`; `WATCHTOWER_MODEL` stays as a fallback); the ADR 0001 table and
  README rows land with T-035, which implements them
- [ ] Interfaces between T-032…T-036 named, so they can be built in parallel
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [0003](docs/decisions/0003-template-format.md), [0010](docs/decisions/0010-agent-runtime-and-tools.md)

### T-032 — Browser sessions: actions, long polite boot, network capture
**Acceptance:**
- [ ] One context per read; the actions listed in T-030 §1; per-portal script cache
- [ ] Time budget excludes limiter waits: tenderned.nl's list renders (probe: 95 requests) without a timeout
- [ ] Network log per action; limiter still spaces every sub-request (test)
**Notes:** the branch is stacked on `docs/T-031-engine-v2-adrs` (it implements ADR 0014 §1). Merge T-031 first; then
the T-032 PR shows only its own two commits.
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [network](docs/net/network.md)

### T-033 — Template format v2 + executor
**Acceptance:**
- [ ] Executes v2 (`entry`, `flow`, `api` + `bootstrap`, `scope`, `items.group`, normalisers); v1 unchanged
- [ ] New mock portal kind: home → link → list with "load more" and detail tabs, with a JSON API behind it, plus variants
- [ ] Hand-written v2 templates read tenderned.nl and the new mock portal correctly
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [executor](docs/engine/executor.md)

### T-034 — Agent v2 explorer
**Acceptance:**
- [ ] Browser tools from T-030 §3; produces v2 templates; prefers API mode when a replay matches
- [ ] Attaches the new mock portal kind and tenderned.nl with a scripted model, and live with a real model
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [attach](docs/agent/attach.md), [toolkit](docs/agent/toolkit.md)

### T-035 — Agent v2 model tiers
**Acceptance:**
- [ ] Strong/light settings and budgets; light tasks are tools of the strong model, which reviews their output
- [ ] Every model call still goes through `agent` and is tagged with its tier
- [ ] `WATCHTOWER_MODEL_STRONG` / `WATCHTOWER_MODEL_LIGHT` in the ADR 0001 env table and the README
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [runtime](docs/agent/runtime.md)

### T-036 — Completeness
**Acceptance:**
- [ ] Declared-total and widget checks; notice-type scope check; partial reads never promote to `serving`
- [ ] Regression fixture for the evergabe widget
**Notes:** the truthful-blocks half moved to T-039 (it doesn't need the v2 executor).
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [read validation](docs/validate/checks.md)

### T-039 — Truthful blocks (split from T-036)
**Acceptance:**
- [ ] `report_blocked` accepted only with the evidence in [ADR 0015 → Truthful blocks](docs/decisions/0015-agent-v2-explorer-and-tiers.md#truthful-blocks-t-036): the block on ≥ 2 tenders, tried in the list page's session, anonymous routes tried, or a whole-site block on the entry page; otherwise it answers with what's missing
- [ ] Constant gateway/WAF 403 on every path → block reason ("access refused by the portal's firewall"), not temporary
- [ ] Regression fixtures for the dtvp.de and PLACE false blocks (login redirect on one document link, anonymous route available)
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [ADR 0015](docs/decisions/0015-agent-v2-explorer-and-tiers.md), [toolkit](docs/agent/toolkit.md)

### T-037 — Investigate/repair and audits on v2
**Acceptance:**
- [ ] Repair of a changed flow step, selector or endpoint on the new mock portal's variants
- [ ] Audits compare against the declared total
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic), [investigate](docs/repair/investigate.md), [health checks](docs/notice/health-checks.md)

### T-038 — v2 live re-run
**Acceptance:**
- [ ] The demo scenario ([E2E and smoke runs](docs/test-portals/e2e-and-smoke.md)) on v2 meets T-030's acceptance; results in the registry
- [ ] Then the final run against the `unseen` portals (moved here from T-020)
**Refs:** [T-030](#t-030--engine-v2--agent-v2-epic)

### T-021 — README (split from the demo script)
**Acceptance:**
- [ ] One-page README: how it works, env vars, how to run it, and a cost-per-portal section that T-099 fills in
**Refs:** [requirements → Hand-in](docs/spec/requirements.md#hand-in)
**Notes:** the demo video script moved to T-040 (it needs the v2 live re-run, T-038). The README can describe v2 only
where it is merged; T-033/T-034 update it as they land.

### T-040 — Demo video script (split from T-021)
**Acceptance:**
- [ ] Shot list for a video of at most 3 minutes, including the dashboard
**Refs:** [requirements → Hand-in](docs/spec/requirements.md#hand-in)

### T-099 — LLM usage metering & cost per portal
**Goal:** Measure tokens and cost per portal (attach, investigate) for the README and the "Approach" criterion.
**Acceptance:**
- [ ] Usage recorded per run from Agents SDK usage data; shown on the dashboard
- [ ] `docs/cost/model-usage.md` and the README state the cost of one portal
**Refs:** [model-usage](docs/cost/model-usage.md)
**Notes:** Deliberately last.

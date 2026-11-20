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
| T-009 | Engine executor (`run_template`) | engine | review | OrangeCanvas705 | feat/T-009-engine-executor | T-007, T-008 | [0002](docs/decisions/0002-architecture.md) |
| T-010 | Normalizers & tender content hash | engine | todo | | | T-003 | [api-contract → Tender](docs/spec/api-contract.md#tender) |
| T-011 | Validators (`validate.check`) | notice | todo | | | T-009 | [0002](docs/decisions/0002-architecture.md) |
| T-012 | Document downloader & `/files` store | documents | todo | | | T-004, T-007 | [api-contract → Tender](docs/spec/api-contract.md#tender) |
| T-013 | Mock portals + mutated variants | test-portals | done | OrangeCanvas705 | feat/T-013-mock-portals | T-003 | [requirements](docs/spec/requirements.md#finding-test-portals) |
| T-014 | Agent runtime & tools | agent | todo | | | T-007, T-009 | [0001](docs/decisions/0001-tech-stack.md), [0002](docs/decisions/0002-architecture.md) |
| T-015 | Scenario `generate_template` (Attach) | agent | todo | | | T-013, T-014 | [0002](docs/decisions/0002-architecture.md) |
| T-016 | Scenario `investigate` (Notice → Repair) | repair | todo | | | T-011, T-013, T-014 | [0002 lifecycle](docs/decisions/0002-architecture.md#portal-lifecycle) |
| T-017 | Read pipeline wiring (attach / read / heal flows) | core | todo | | | T-006, T-009, T-010, T-011, T-012 | [0002 read pipeline](docs/decisions/0002-architecture.md#read-pipeline-no-model-call) |
| T-018 | Dashboard | dashboard | todo | | | T-005 | [requirements](docs/spec/requirements.md) |
| T-019 | Docs site at `/docs` | docs-site | todo | | | T-003 | [0001](docs/decisions/0001-tech-stack.md) |
| T-022 | Deterministic agent toolkit | agent | todo | | | T-007, T-009, T-010 | [0002 toolkit](docs/decisions/0002-architecture.md#agent-toolkit-deterministic-first) |
| T-023 | Health-check modes (reactive / proactive audit) | notice | todo | | | T-006, T-016, T-017 | [0002 health checks](docs/decisions/0002-architecture.md#health-check-modes) |
| T-020 | End-to-end tests & real-portal smoke run | test-portals | todo | | | T-001, T-015, T-016, T-017, T-023 | [requirements → Judging](docs/spec/requirements.md#judging) |
| T-021 | README & demo video script | docs-site | todo | | | T-020 | [requirements → Hand-in](docs/spec/requirements.md#hand-in) |
| T-099 | LLM usage metering & cost per portal (last MVP task) | cost | todo | | | T-020 | [model-usage](docs/cost/model-usage.md) |

**Critical path:** T-003 → T-007/T-008 → T-009 → T-014 → T-015/T-016 → T-017 → T-023 → T-020 → T-099.
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
- [ ] Agents SDK setup reading `OPENAI_API_KEY` / `WATCHTOWER_MODEL`; the only place model calls happen
- [ ] Core tools: `fetch` (pruned outline), `render` (captured JSON/XHR summary), `try_template` (engine dry run), `submit_template`, `report_blocked`, `report_transient`
- [ ] A tool registry that T-022 extends by adding tools, without editing scenario code
- [ ] HTML pruning before content reaches the model; per-scenario budgets (turns, tokens, wall time)
**Refs:** [0002](docs/decisions/0002-architecture.md)

### T-015 — Scenario `generate_template`
**Acceptance:**
- [ ] Returns an `AttachOutcome`; handles summary-page links (finds the real tender list); detects login/captcha → `Blocked`
- [ ] Doesn't report `Blocked` when a captcha is shown but the list and documents still download without it (try first, then decide)
- [ ] Produces a validated template for every mock portal kind
**Refs:** [0002](docs/decisions/0002-architecture.md)

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
- [ ] Discovery:
  - `find_repeating_blocks` (candidate tender-list containers + item count + sample)
  - `find_pagination`
  - `classify_links` (detail pages vs documents; multilingual keywords such as Vergabeunterlagen, DCE, pliegos, dokumentacja)
  - `resolve_tender_page` (summary/notice page → the real tender page)
  - `summarize_json` (keys, types, array lengths of a captured endpoint)
- [ ] Recognition:
  - `fingerprint_software` (URL path, footer, `meta generator`) + `find_template_for_software` (reuse a template from another portal with the same fingerprint)
  - `map_field_labels` (multilingual synonym dictionary → contract fields)
  - `probe_dates` (format + time zone from sample strings and the country)
  - `detect_block` (login/captcha; also checks whether the documents download anyway)
- [ ] Verification:
  - `test_selector` (CSS/XPath/JSONPath/regex on a sample)
  - `try_template` with field-coverage stats
  - `probe_document` (content type, size, filename, HTML-instead-of-file)
- [ ] Repair:
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
- [ ] Final run against the `unseen` portals
**Refs:** [requirements → Judging](docs/spec/requirements.md#judging)

### T-021 — README & demo video script
**Acceptance:**
- [ ] One-page README: how it works, env vars, cost per portal (filled in from T-099)
- [ ] Shot list for a video of at most 3 minutes, including the dashboard
**Refs:** [requirements → Hand-in](docs/spec/requirements.md#hand-in)

### T-099 — LLM usage metering & cost per portal
**Goal:** Measure tokens and cost per portal (attach, investigate) for the README and the "Approach" criterion.
**Acceptance:**
- [ ] Usage recorded per run from Agents SDK usage data; shown on the dashboard
- [ ] `docs/cost/model-usage.md` and the README state the cost of one portal
**Refs:** [model-usage](docs/cost/model-usage.md)
**Notes:** Deliberately last.

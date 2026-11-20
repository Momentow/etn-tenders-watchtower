# 0001 — Tech stack

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-002

## Context
One container on port 8080 must serve the API, the dashboard and the docs, scrape many portals politely
(≤ 1 req/s each), download large files, and run LLM-powered scenarios to learn, check and repair portal readers. The
workload is I/O-bound: politeness caps throughput long before CPU does. The team is 3–4 people with a 24h budget.

## Decision

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12+**, `asyncio` everywhere | Best scraping and LLM ecosystem; one language for API, engine and agent |
| Packaging | `uv` (`pyproject.toml` + `uv.lock`) | Fast, reproducible installs; fast Docker builds |
| API server | FastAPI + uvicorn, single process | Async; pydantic request/response models mirror [api-contract](../spec/api-contract.md) |
| HTTP | `httpx` (async, streaming) | Streaming downloads to disk, HTTP/2, timeouts |
| Parsing | `selectolax` (CSS), `lxml` (XPath), `jsonpath-ng` (JSON) | Fast and declarative, so they map directly onto template selectors |
| Browser | Playwright + Chromium, base image `mcr.microsoft.com/playwright/python` | JS-only portals and network/XHR capture; the prebuilt image keeps builds fast |
| Database | SQLite (WAL) via `aiosqlite`, plain SQL migrations | No extra service, low RAM, survives restarts |
| Files | Content-addressed on disk under `$DATA_DIR/files/<sha256>` | Streamed, no RAM buffering, dedup for free |
| Agent runtime | **OpenAI Agents SDK** with our own tools | Agent loop, tool calling, turn limits, structured output and token usage out of the box |
| Dashboard | Server-rendered Jinja2 + htmx | No JS build step; live refresh by polling partials |
| Docs site | **Zensical** reading `mkdocs.yml` (fallback: `mkdocs-material` 9.7.x pinned), built during `docker build`, served statically at `/docs` | Modern minimal product-docs look; Material for MkDocs is in maintenance mode and Zensical is its successor |
| Lint/format | `ruff` | One tool, fast |
| Tests | `pytest` + `pytest-asyncio`, local mock portals | Offline, deterministic Notice/Repair tests |

**Environment variables:**

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | Model key |
| `WATCHTOWER_MODEL` | set in T-014 | Model name used by the agent |
| `DATA_DIR` | `/data` | SQLite database and stored files |
| `WATCHTOWER_HEALTHCHECK_MODE` | `proactive` | `reactive` or `proactive`; see [ADR 0002](0002-architecture.md#health-check-modes) |
| `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` | `24` | Interval of the scheduled audit (proactive mode only) |
Exact library versions are pinned in `uv.lock` by T-003.

## Rejected
- **Go:** faster, but the bottleneck is politeness, not CPU. Playwright for Go is community-maintained, and LLM/agent tooling is
  thinner. It would split the codebase into two languages.
- **Amazon Infinity:** an early research release (Rust) built for tens of thousands of concurrent agents, using an external
  conversation store. We need a handful of agents; it would add a third language and an extra service.
- **Hand-written agent loop:** re-implements what the Agents SDK already provides (tool loop, limits, usage).
- **Redis/Celery, Postgres:** extra services and RAM for no benefit at our scale.

## Consequences
- Every module is async; blocking calls (heavy parsing, unzip) go through `asyncio.to_thread`.
- The image includes Chromium (a few hundred MB). Templates prefer plain HTTP/JSON so regular reads rarely need it.
- The docs build runs in the Dockerfile. A docs syntax error breaks `docker build`, which is intended.

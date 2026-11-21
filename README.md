# Watchtower

Watchtower connects to public-procurement portals on its own. Give it nothing but a portal's address and it
**attaches** (delivers every open tender with its documents), **notices** when delivery breaks and says so truthfully,
and **repairs** itself after the portal changes. No person steers it while it runs. A dashboard at `/` shows which
portals deliver, which broke and what the system did about it.

Our entry for the Patterno hackathon track. Full docs are served by the container at
[`/docs`](http://localhost:8080/docs/) (source in [`docs/`](docs/index.md)).

## How it works

**An LLM learns each portal once; a deterministic engine reads it from then on.** The agent never writes code. It
writes a **template**: declarative data that says where the tender list is, how to page through it, which fields to
take from list and detail pages, and where the documents are ([ADR 0003](docs/decisions/0003-template-format.md),
[v2](docs/decisions/0014-template-format-v2.md)). The engine runs the template with no model call.

```
POST /portals ─► attach: agent explores the portal ─► template ─► engine dry run + validation ─► serving
                                                                                      └─ login/captcha/unreadable ─► failed

every hour  ─► engine reads the portal (no model) ─► validate ─► store tenders, download documents ─► serving
                                                       └─ fails ─► healing ─► investigate ─► repaired template ─► serving
                                                                                 └─ blocked / gone / unrepairable ─► failed
every 24 h  ─► proactive audit: agent compares engine output with the live portal (silent breakage) ─► healthy or repair
```

- **Attach** (`generate_template`). The agent explores the portal with deterministic tools: pruned page outlines,
  list and pagination detection, captured JSON/XHR endpoints, field-label mapping in many languages, date and time-zone
  probes, login and captcha detection, document download probes, and a `try_template` dry run. The model makes the
  judgement calls; the tools do everything that can be computed ([agent toolkit](docs/agent/toolkit.md)). A strong model
  plans and decides; a light model takes narrow, repetitive checks ([model tiers](docs/agent/runtime.md#model-tiers)).
- **Read.** The engine fetches the list (HTML, browser-rendered pages, or the portal's own JSON API), follows detail
  pages, and normalises values: deadlines become ISO 8601 with an offset, CPV codes and enums are cleaned, and a field
  the portal doesn't show is `null`, never a guess ([engine](docs/engine/executor.md), [normalisers](docs/engine/normalize.md)).
- **Notice.** Every read is checked against the portal's history (a sharp drop in the tender count, a field that
  goes missing, invalid or implausible dates, deadlines that all jump at once) before anything is stored ([read validation](docs/validate/checks.md)). A failed check moves the portal to
  `healing`, and **the last correct tenders keep being served**. Audits catch breakage a check can't see
  ([health checks](docs/notice/health-checks.md)).
- **Repair** (`investigate`). Deterministic checks run first (a transient error, a block page, a structural diff
  against the last good read). The model only runs if they can't explain the failure. A new template is promoted only
  after it passes validation; old versions are kept ([investigate](docs/repair/investigate.md)).
- **Documents** are streamed to disk, stored by SHA-256 and served at `/files/{id}`. ZIPs are unpacked; files over
  250 MB are skipped ([downloader](docs/documents/downloader.md)).
- **Politeness.** All portal traffic goes through one network layer: at most 1 request per second per portal host
  across all workers, no logins, no captcha solving. A portal behind a login or captcha ends `failed` and its `reason`
  says so ([network layer](docs/net/network.md)).

Everything runs in one Python process (FastAPI, asyncio workers, SQLite job queue that survives restarts):
API, dashboard, docs, scheduler and workers. Module boundaries: [ADR 0002](docs/decisions/0002-architecture.md).
The HTTP API follows [the contract](docs/spec/api-contract.md) exactly.

## Run

```bash
docker build -t watchtower .
docker run -p 8080:8080 -e OPENAI_API_KEY -v watchtower-data:/data watchtower
```

Open <http://localhost:8080/> for the dashboard and <http://localhost:8080/docs/> for the docs. Add a portal:

```bash
curl -X POST localhost:8080/portals -H 'content-type: application/json' -d '{"url": "https://portal.example/tenders"}'
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | required | Model key. Read from the environment only |
| `WATCHTOWER_MODEL_STRONG` | `WATCHTOWER_MODEL`, then `gpt-5.6-luna` | Strong tier: explores portals, writes and repairs templates, decides outcomes |
| `WATCHTOWER_MODEL_LIGHT` | the strong model | Light tier: narrow checks the strong model delegates and reviews |
| `WATCHTOWER_MODEL` | `gpt-5.6-luna` | Sets both tiers when the two above are unset |
| `DATA_DIR` | `/data` | SQLite database and downloaded files |
| `WATCHTOWER_HEALTHCHECK_MODE` | `proactive` | `reactive`: investigate only when a read fails. `proactive`: also audit every serving portal on an interval |
| `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` | `24` | Audit interval in proactive mode |

The same table, with rationale, is in [ADR 0001](docs/decisions/0001-tech-stack.md).

## Model cost per portal

Regular reads make **no model call**. The model runs only to attach a portal, to investigate a failed read, and (in
proactive mode) once per audit interval per portal. So the cost of one portal is roughly one attach, plus one audit per
day, plus an occasional repair.

Measured tokens and cost per portal: _not measured yet; filled in by T-099 from real runs
([model usage](docs/cost/model-usage.md))._

## Development

Install [uv](https://docs.astral.sh/uv/); Python 3.12 is pinned in `.python-version`.

```bash
uv sync                                   # dependencies, including dev tools
uv run python -m watchtower               # the service on :8080
uv run python -m mockportals              # local fake portals on :8081, with mutated variants
uv run pytest                             # all tests, offline (a scripted model stands in for the LLM)
uv run ruff check . && uv run ruff format .
uv run --group docs zensical build        # the docs site
```

**Model smoke test.** Put the key in `.env` (copy `.env.example`), then check the key, the model (and the light model
when it differs), tool calling and structured output with one real call: `uv run --env-file .env python -m watchtower.agent.smoke`.

**Live runs** against mock or real portals, with a results table: [E2E and smoke runs](docs/test-portals/e2e-and-smoke.md).
Test portals and their quirks: [registry](docs/portals/registry.md).

# 0004 — Storage: SQLite schema and `core.store` API

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-004

## Context
ADR 0002 makes `core.store` the only database access layer. The API (T-005), scheduler and state machine (T-006),
documents (T-012), pipeline (T-017) and dashboard (T-018) all read or write through it, so its tables, ids and time
handling have to be fixed before those modules are built.

## Decision

### Connection and migrations
- One SQLite file at `$DATA_DIR/watchtower.db`, opened at app startup (`app.state.store`). WAL mode, foreign keys on.
- One aiosqlite connection per process; every store call takes an asyncio lock and runs in its own `BEGIN IMMEDIATE`
  transaction. SQLite writes are fast next to the 1 req/s politeness limit, so this is not a bottleneck.
- Migrations are SQL scripts in `watchtower/core/migrations.py`, applied in order at startup and counted with
  `PRAGMA user_version`. Add a new entry to change the schema; never edit a shipped one.

### Ids and times
- **Portal id** = first 12 hex chars of `sha256(normalize_url(url))`. The same address always gets the same id, even in a
  fresh database. `normalize_url` lower-cases scheme and host, drops the default port, fragment and trailing slash, sorts
  query parameters and assumes `https://` when no scheme is given. Path case, `www.` and query values are kept.
- **Run id**: random UUID hex. **Job id**: integer. **File id** in `/files/{fileId}`: the file's sha256 (content-addressed,
  matching `$DATA_DIR/files/<sha256>`).
- All times are timezone-aware and stored as fixed-width UTC text (`2026-11-20T10:00:00.000000+00:00`), so text order is
  time order. Naive datetimes are rejected. The API may render them in any offset.

### Tables
| Table | Key | Holds |
|---|---|---|
| `portals` | `id` | `url` (as submitted), `normalized_url` (unique), `state`, `reason`, `last_success_at`, `current_template_version`, `deleted_at` |
| `template_versions` | `(portal_id, version)` | Template JSON, notes, `promoted_at`. Versions count from 1; only a promoted version becomes current |
| `runs` | `id` | `kind` (`read`/`repair`), start/finish, `result`, `template_version`, `tender_count`, `details` JSON (internal: trigger, problems, stats, diagnosis), `snapshot` JSON (list-page structure for the repair diff) |
| `tenders` | `(portal_id, tender_id)` | Contract fields as JSON (`data`), `content_hash`, `status`, `first_seen_at`, `last_seen_at`, `last_changed_at` |
| `documents` | `id`; unique `(portal_id, tender_id, source_url, archive_member)` | One row per stored file; a ZIP gives one row per unpacked member. `file_name`, `size_bytes`, `sha256` |
| `jobs` | `id` | `kind` (`attach`/`read`/`investigate`/`download`), `portal_id`, `payload` JSON, `status` (`queued`/`running`/`done`/`failed`/`cancelled`), `attempts`, `run_after`, `run_id`, `error` |

### Behaviour the store guarantees
- `add_portal` returns `(portal, created)`. Re-adding a deleted address revives the same id in `integrating`.
- `delete_portal` sets `deleted_at` and cancels the portal's queued jobs. Deleted portals are hidden from `get_portal`
  and `list_portals`. Workers call `is_portal_deleted` before each request.
- `tender_count` on a portal counts all its stored tenders.
- `finish_run(..., "ok")` sets the portal's `last_success_at`. `details` passed to `finish_run` are merged.
- `upsert_tenders` moves `last_changed_at` when a tender is new or its content hash **or status** changed, and
  `last_seen_at` for every tender passed in. `close_unlisted_tenders` closes `open` tenders missing from a read.
  `list_tenders(changed_since=…)` is strictly-later.
- `claim_next_job` atomically takes the oldest due queued job. `requeue_running_jobs` (called once at startup by the
  scheduler) puts jobs left `running` by a crash back in the queue.

### What the store does *not* decide
Policy stays with its owners (ADR 0002): which state and reason to set (state machine), when a read counts as failed and
whether tenders get stored (pipeline: only validated reads), the content hash (engine), the one-active-job-per-portal
rule (scheduler, using `active_jobs`), and when a deadline closes a tender (pipeline passes `status="closed"`).

## Consequences
- Modules code against `watchtower.core.store.Store` and the records in `watchtower.core.models`.
- The contract's camelCase field names live only in the API layer; the store uses snake_case.
- Schema changes need a new migration and an update to this table (or a superseding ADR if behaviour changes).

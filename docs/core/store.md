# Store (`watchtower.core.store`)

The only code that touches the database. Design and guarantees: [ADR 0004](../decisions/0004-storage.md).

## Using it

The app opens the store at startup (`$DATA_DIR/watchtower.db`, migrations applied) and puts it on `app.state.store`.
In tests, open one on a temporary file:

```python
store = await Store.open(tmp_path / "w.db")
portal, created = await store.add_portal("https://tenders.example.de/list")
```

## Calls by caller

| Caller | Calls |
|---|---|
| API (T-005) | `request_run`, `add_portal`, `get_portal`, `list_portals`, `delete_portal`, `list_runs`, `get_running_run`, `list_tenders(changed_since=…)`, `get_file` |
| State machine (T-006) | `set_portal_state` (the only caller) |
| Scheduler and workers (T-006) | `request_run` (attach/read/investigate, [ADR 0005](../decisions/0005-run-requests.md)), `enqueue_job` (downloads), `claim_next_job`, `finish_job`, `retry_job`, `set_job_run`, `active_jobs`, `requeue_running_jobs`, `is_portal_deleted` |
| Pipeline (T-017) | `start_run`, `finish_run`, `upsert_tenders`, `close_unlisted_tenders`, `add_template_version`, `promote_template_version`, `get_current_template`, `last_good_snapshot` |
| Documents (T-012) | `add_document` (after the file is on disk) |
| Dashboard (T-018) | `list_portals`, `list_runs`, `list_template_versions`, `list_tenders` |

Records (`Portal`, `Run`, `TemplateVersion`, `StoredTender`, `DocumentFile`, `Job`) are pydantic models in
`watchtower.core.models`. All datetimes are timezone-aware UTC.

## Changing the schema

Append a new SQL script to `MIGRATIONS` in `watchtower/core/migrations.py`, add store methods next to the existing
ones, and update the tables in ADR 0004 in the same PR.

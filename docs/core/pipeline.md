# Read pipeline

`watchtower.core.pipeline` is where a job becomes delivered tenders. It registers the `attach`, `read` and `investigate`
job handlers, runs the engine, checks the result, stores the tenders and reports one event to the state machine.
The outcome rules are fixed in [ADR 0009](../decisions/0009-pipeline-and-agent-outcomes.md); the lifecycle is in
[ADR 0002](../decisions/0002-architecture.md#portal-lifecycle).

## Flows

| Job | Portal | Steps | Ends with |
|---|---|---|---|
| `attach` | `integrating` | `generate_template` → store as new version → engine run → `validate.check` (no history) → promote → deliver | `attach_succeeded` / `attach_failed` |
| `read` | `serving` | engine run with the current template → `validate.check` against history → deliver | `read_succeeded` / `start_healing` |
| `investigate` (repair) | `healing` | audit candidate → plain re-read → `investigate(trigger="failure")` → re-read or validate the new template. After an audit finding, a re-read that passes doesn't count as a repair ([ADR 0012](../decisions/0012-audit-finding-repair.md)) | `repair_succeeded` / `repair_failed` |
| `investigate` (audit) | `serving` | re-read → `investigate(trigger="audit")` | `audit_healthy` / `start_healing(found_by_audit=True)` |

A job whose portal was deleted, or isn't in the state the flow expects, closes its run as `failed`
(`details.cancelled` / `details.skipped`) and does nothing else.

## Delivering a validated read
1. Duplicate ids keep the first; an already-stored tender that came back incomplete keeps its stored version.
2. Open tenders past their deadline are stored as `closed`.
3. `upsert_tenders` moves `lastChangedAt` for new and changed tenders; tenders no longer listed are closed (skipped when
   pagination hit `max_pages`).
4. Download jobs for new tenders with documents and for every changed tender.
5. The run is finished `ok` with the tender count, the list-page snapshot and `details`
   (`template_version`, `stats`, `tenders: {new, changed, unchanged, closed, kept_incomplete}`).

A failed read stores nothing: its run gets `details.problems`, and the first problem becomes the portal's `reason`.
Runs that got an agent diagnosis also store `details.diagnosis` (its kind) and `details.diagnosis_reason` (its reason or
notes); audits add `details.audit = true`, and a repair started by an audit `details.audit_finding` (the audit run's id).

## Agent scenarios
The pipeline calls the agent only through `Scenarios(generate_template, investigate)`, set with
`pipeline.configure(...)` in `app.py`: `generate_template` is the [attach scenario](../agent/attach.md) (T-015) and
`investigate` is the [investigate scenario](../repair/investigate.md) (T-016). The stubs in `watchtower/agent/stubs.py`
make no model call and remain the defaults when `configure` is given no scenarios (tests).

## Testing
`tests/core/test_pipeline.py` runs every flow on the mock portals (`ssr` kind, including the `markup` variant) with fake
scenarios that return chosen outcomes:

```
uv run pytest tests/core/test_pipeline.py
```

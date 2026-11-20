# 0006 — Job handlers and state-machine events

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-006

## Context
The scheduler (T-006) runs jobs; the read pipeline (T-017) and the document downloader (T-012) do the work, and they are
built later. They need a fixed way to plug in, and one way to report outcomes so that only the state machine writes
`state` and `reason` (ADR 0002).

## Decision

### Handlers
- A handler is `async def handler(ctx: JobContext) -> None`, registered once per job kind with
  `@watchtower.core.handlers.register("attach" | "read" | "investigate" | "download")`.
- `JobContext` gives `store`, `states` (the `StateMachine`), `job`, `run_id` (ADR 0005), `trigger` and
  `portal_deleted()`. Handlers check `portal_deleted()` before each portal request and stop when it's true.
- Until a kind has a handler, its jobs stay queued.
- **An attach/read/investigate handler must, before returning:**
  1. finish `ctx.run_id` with `store.finish_run` (`ok`/`failed`, `tender_count`, `details`, and `snapshot` for good
     reads), then
  2. report the outcome with one `ctx.states.apply(...)` or `ctx.states.start_healing(...)` call (table below).
- Returning normally means the job is done. Raising (or running past the kind's timeout) means the attempt failed: the
  job is retried with the same run. After the last attempt the scheduler closes the run as `failed` and applies the
  failure event itself (`attach_failed`, start healing, or `repair_failed`). A crashed audit changes nothing.

### Events (`StateMachine.apply(portal_id, event, detail=…, tender_count=…)`)
| Event | From → to | Used when |
|---|---|---|
| `attach_succeeded` | integrating → serving | `TemplateFound` and the first read validated |
| `attach_failed` | integrating → failed | `Blocked` / `Unreadable` / attach budget exhausted |
| `read_succeeded` | serving → serving | a read passed validation |
| `read_failed` | serving → healing | a read failed (use `start_healing`, which also queues the repair) |
| `audit_healthy` | serving → serving | audit → `Healthy` |
| `audit_found_problem` | serving → healing | audit found a silent problem (`start_healing(found_by_audit=True)`) |
| `repair_succeeded` | healing → serving | `Transient` re-read ok, or `Changed` template validated and promoted |
| `repair_failed` | healing → failed | `Blocked` / `Gone` / `Unrepairable` / repair budget exhausted |
| `reattach` | failed → integrating | applied by the scheduler before an attach job on a failed portal |

Any other pair raises `InvalidTransition` and changes nothing. `detail` is a short phrase; the state machine builds
the full `reason` sentence.

### Scheduling
- Every attach/read/investigate request goes through `Store.request_run` (ADR 0005). Triggers used by the scheduler
  are `scheduled` (periodic read), `audit` (investigate job, recorded as a `read` run), `failure` and `audit_finding`
  (repairs).
- Periodic read: a `serving` portal whose last finished read is older than `read_interval` (1 h).
- Audits (`proactive` mode only): first due after `interval × (1 + a stable per-portal fraction)`, then one interval
  after the previous audit. At most one is started per tick (15 s), so audits are staggered. `Scheduler.due_audits()`
  lists the due ones for T-023.
- Deleted portals: every tick cancels running jobs of deleted portals and closes their runs; queued ones were
  cancelled on delete (ADR 0005). That's well within the contract's one minute.

## Consequences
- T-017 registers the attach/read/investigate handlers and calls the agent scenarios, engine, validator and store from
  them. T-012 registers `download`. T-023 builds on `due_audits` and the `audit` trigger.
- Defaults (`SchedulerSettings`): 4 workers, retries `attach`/`read`/`investigate` 2 attempts and `download` 3, timeouts
  30–60 min per kind. Tests pass their own settings.

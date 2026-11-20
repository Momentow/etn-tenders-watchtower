# 0005 — Requesting work: runs are created when work is queued

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-005

## Context
`POST /portals/{id}/refresh` must answer **at once** with a `runId`, and with the *running* read's `runId` if one is
already running. So the run has to exist before any worker picks the job up. The API (T-005) and the scheduler and
workers (T-006) are built in parallel and need one rule for who creates runs.

## Decision
- All attach/read/investigate work is requested with **`Store.request_run(portal_id, job_kind, trigger=…)`**. In one
  transaction it either:
  - returns the portal's unfinished run (read **or** repair) with `created=False` and queues nothing, or
  - starts a run (`attach`/`read` → `read` run, `investigate` → `repair` run) with `details.trigger`, and queues a
    job of `job_kind` with `payload = {"run_id": …, "trigger": …}` and `jobs.run_id` set.
- **Workers never start their own run for these jobs.** They take `payload.run_id` and finish that run with
  `Store.finish_run`. `download` jobs have no run.
- Because any unfinished run blocks a new request, this also enforces *at most one active attach/read/investigate per
  portal*. A refresh during `healing` returns the repair run's id; the contract only says "a read".
- Triggers used so far: `attach` (`POST /portals`) and `refresh` (`POST /portals/{id}/refresh`). The scheduler uses its
  own, e.g. `scheduled`, `audit` and `failure`.
- A refresh on a `failed` portal requests an `attach` job. The worker's state machine moves the portal to
  `integrating` (ADR 0002 lifecycle); the API never writes `state` or `reason`.
- `delete_portal` cancels queued jobs **and** closes their reserved runs as `failed` (`details.cancelled`), so no run
  stays open forever.

## Consequences
- T-006: when a worker finishes an attach/read job and the pipeline decides the portal must heal, it calls
  `request_run(portal_id, "investigate", trigger="failure")` *after* finishing the read run. Periodic reads and audits
  also go through `request_run`.
- If a worker dies mid-job, `requeue_running_jobs` puts the job back and the same run continues.

# 0008 — Read validation: `PortalHistory` and the checks

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-011

## Context
ADR 0002 fixes `check(result: ReadResult, history: PortalHistory) -> Health(ok, problems)` but leaves `PortalHistory`
undefined. The pipeline (T-017), the repair scenario (T-016) and the audit (T-023) all depend on what it contains and on
what "a read is trustworthy" means.

## Decision
- `watchtower.validate.PortalHistory` (pydantic):
  - `recent_counts: list[int]`: tender counts of the latest 5 good reads, newest first;
  - `field_fill: dict[str, float]`: the share of currently served tenders with each watched required field filled
    (`url`, `title`, `buyerName`, `publicationDate`, `submissionDeadline`);
  - `deadlines: dict[str, str]`: tender id → the deadline we serve now.
- The pipeline builds it with `PortalHistory.from_store(await store.list_runs(pid), await store.list_tenders(pid))`.
  Good reads are `read` runs with result `ok` and a `tender_count`. Served tenders are those not `closed`. An empty
  history (`PortalHistory()`) means a first read (attach).
- `check(result, history=None, *, today=None)` is pure: no network, DB or model. `Health.ok` is true only when
  `problems` is empty. Any problem fails the read: it isn't stored, and the portal goes to `healing`.
- The checks and their thresholds are listed in [validate/checks.md](../validate/checks.md). They are module constants in
  `watchtower/validate/health.py`.

## Consequences
- T-017 must call `finish_run(..., tender_count=len(result.tenders))` on good reads, or the drop check has no baseline.
- Problems are complete sentences, in English, with numbers. T-017 can use the first one (or all of them) as `reason`.
- Silent errors that don't change counts, fields or dates (e.g. the buyer column now shows the city) aren't caught
  here. That's the proactive audit's job (T-023).

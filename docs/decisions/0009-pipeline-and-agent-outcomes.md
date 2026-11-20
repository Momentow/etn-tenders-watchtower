# 0009 — Read pipeline and the agent scenario outcomes

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-017

## Context
ADR 0002 names the scenario signatures and their outcomes (`AttachOutcome`, `Diagnosis`) but not as importable types.
The pipeline (T-017) calls the scenarios while T-014/T-015/T-016 build them in parallel, so both sides need the same
types now, and a rule for what the pipeline does with each outcome.

## Decision

### Outcome types: `watchtower/agent/outcomes.py`
Pydantic models with a `kind` tag; `reason`/`notes` are short plain-language phrases (they end up in the portal's `reason`).

| Scenario | Returns |
|---|---|
| `generate_template(portal_url)` | `TemplateFound(template, notes)` · `Blocked(reason)` · `Unreadable(reason)` |
| `investigate(portal: PortalView, template, result, health, *, trigger)` | `Healthy(notes)` (audit only) · `Transient(reason)` · `Changed(new_template, reason)` · `Blocked(reason)` · `Gone(reason)` · `Unrepairable(reason)` |

`PortalView` is the read-only portal summary the agent gets: `id`, `url`, `state`, `template_version`,
`last_good_snapshot`, `recent_counts`. The `GenerateTemplate` and `Investigate` protocols give the exact signatures.

### Wiring
- `watchtower.core.pipeline` registers the `attach`, `read` and `investigate` handlers.
- `pipeline.configure(client=, browser=, scenarios=Scenarios(generate_template, investigate))` is called once in
  `app.py`. Until T-015/T-016 merge, `watchtower/agent/stubs.py` is used: attach → `Unreadable`, investigate →
  `Transient` (failure) / `Healthy` (audit). **T-015/T-016 switch `app.py` to the real scenarios.**
- A scenario that raises fails the job attempt; the scheduler retries and then applies the failure event (ADR 0006).

### What the pipeline does with each outcome
- **Attach:** `TemplateFound` → stored as a new unpromoted version → engine run + `validate.check` with an empty history
  → promoted and delivered (`attach_succeeded`), or `attach_failed`. `Blocked`/`Unreadable` → `attach_failed`.
- **Repair** (`healing`): (1) for `trigger=audit_finding`, the newest unpromoted version proposed by the audit is tried
  first; (2) a plain re-read with the current template, so a temporary failure is repaired without a model call;
  (3) `investigate(trigger="failure")`:
  `Transient` → one more re-read, else `repair_failed` · `Changed` → new version, promoted only if it validates, else
  `repair_failed` · `Blocked`/`Gone`/`Unrepairable` → `repair_failed` with its reason · `Healthy` → `repair_failed`.
- **Audit** (`serving`, a `read` run): a re-read that fails validation is an ordinary failed read (`start_healing`).
  Otherwise `investigate(trigger="audit")`: `Healthy`/`Transient` → delivered, `audit_healthy`; anything else →
  `start_healing(found_by_audit=True)`. A `Changed` template is stored unpromoted for step (1) of the repair.

### Tender rules
- Only validated reads are stored. Duplicate ids within a read keep the first.
- An open tender whose `submissionDeadline` has passed is stored as `closed`. Tenders no longer listed are closed,
  except when pagination stopped at `max_pages` (pages may be left unread).
- An incomplete tender (`complete=False`) that is already stored keeps its stored data and documents.
- Download jobs are queued for new tenders with documents and for every changed tender.
- Run `details`: `template_version`, `stats`, `problems` (failed), `tenders` counts (ok), `diagnosis`, `promoted`, `audit`.

## Consequences
- T-015/T-016 implement the two protocols and return these types; the pipeline needs no change when they land.
- T-023 only adds scheduling refinements: the audit flow already runs for `investigate` jobs with `trigger=audit`.
- A `Transient` diagnosis whose confirming re-read fails ends in `failed`; the portal is not retried later on its own.

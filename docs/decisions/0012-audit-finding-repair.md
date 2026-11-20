# 0012 — Repairing an audit finding (amends 0009)

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-023

## Context
ADR 0009's repair flow is: (1) try the template an audit proposed, (2) a plain re-read with the current template,
(3) `investigate(trigger="failure")`. Step 2 exists so a temporary outage is repaired without a model call. But an
audit only starts a repair when the read **passes validation** and the problem is silent (missed tenders, a wrong
field). For such a repair, step 2 always passes, so the portal went back to `serving` with the reason "the failure was
temporary", while it still served wrong or incomplete data. That breaks the Notice criterion: the reported state must
match what is delivered.

## Decision
For a repair with `trigger="audit_finding"`:

1. The newest unpromoted template proposed by the audit is tried first, as before; if it validates it is promoted
   (`repair_succeeded`, with the audit's diagnosis in the run details).
2. Otherwise the current template is read again:
   - the read **fails validation**: the portal broke visibly in the meantime, so the normal failure flow continues
     (`investigate(trigger="failure")`);
   - the read **passes**: the audit's diagnosis stands. The repair run is `failed` with the audit's reason as its
     problem, and the portal goes to `failed` (`repair_failed`) with that reason. No second model call.

The audit's diagnosis is found on the newest failed audit run (`details.audit`). Every run that got a diagnosis now
also stores `details.diagnosis_reason` (the diagnosis' `reason` or `notes`) next to `details.diagnosis` (its kind), so
the dashboard can say what an audit checked or found.

## Consequences
- An audit finding that the agent can't turn into a validated template ends in `failed` with an exact reason, never in
  a false "temporary" repair. A person has to look, as the contract says for `failed`.
- `refresh` on such a `failed` portal re-attaches it (ADR 0005), as for any failed portal.
- Failure repairs (`trigger="failure"`) are unchanged.

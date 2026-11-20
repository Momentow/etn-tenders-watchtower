# 0007 — Date parsing beyond the template's formats

- **Status:** accepted (amends the date rule of [ADR 0003](0003-template-format.md))
- **Date:** 2026-11-20
- **Task:** T-010

## Context
ADR 0003 says a date rule without `formats` means ISO 8601 only. European portals write dates in many local forms,
and portals add small text around them ("10:00 Uhr", "à 10h00"). With an ISO-only default, the agent would have to
predict every variant, and a tiny change on the portal would turn every deadline into `null`.

## Decision
A date value is read by trying, in order:
1. the template's `formats` (local month names are translated, so `%B` / `%b` work in any of DE/FR/ES/NL/PL/IT/PT/EN);
2. ISO 8601;
3. common European forms, always **day-first**.

Step 3 also runs when `formats` is set but none fits. Day-first is safe because every target portal is European.
Dates outside 2000–2100, impossible days or times, and deadlines without a time of day stay `null`.

## Consequences
- Templates may leave out `formats` for common European dates; `formats` remains the way to pin an unusual one.
- A portal that silently switched to month-first would be misread. That's acceptable for the European scope; such a
  portal needs explicit `formats`, and validators (T-011) flag deadlines that suddenly jump.

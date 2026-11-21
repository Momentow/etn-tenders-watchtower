# 0018: Completeness checks: a partial read is never served

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-036 (epic T-030)
- **Amends:** [0008](0008-read-validation.md) (adds checks), [0002](0002-architecture.md) `ReadResult` (adds fields)

## Context

The first live smoke run served evergabe.nrw.de from its home page's box of the latest 20 notices, as if those were all
its open tenders. About 33 were open, and the box also held award notices and intended-tender announcements. Every
existing check passed: the read had tenders, fields and valid dates, and there was no history to drop from. ADR 0008's
checks compare a read with earlier reads, never with the portal itself.

## Decision

1. **The engine records evidence** (`watchtower/engine/completeness.py`) on every read, in `ReadResult`:
   - `more_offered: list[str]`: only for a list read with **no pagination**. These are the first list page's signs that
     the list holds more:
     - a `rel=next` link, a "next" control or a numbered page control;
     - an "all tenders" / "load more" link or button ("Alle Bekanntmachungen", "View all", "Laad meer"…), recognised by
       wording in several languages;
     - in JSON, a total key larger than the page, or a next-page pointer.

     Controls inside the list items don't count. Without pagination nothing else can show that a page is an excerpt,
     and with pagination the other checks already apply.
   - `other_notices: dict[str, str]`: delivered tender id → the short label (≤ 90 characters: badges, type fields,
     titles, never descriptions) that marks its list item as another notice type. The vocabulary covers several
     languages: contract award, prior information, market consultation, intended tender, early termination and
     voluntary ex-ante notices.
   - `stats.limited`: the read stopped at the caller's `limit`, as the agent's dry runs do.
   - From v2 templates ([ADR 0014](0014-template-format-v2.md)): `declared_total` and `scope_dropped`.
2. **`validate.check` fails the read** (`watchtower/validate/completeness.py`) when any of these holds:
   - **declared total:** tenders delivered + records dropped by scope < 95 % of the portal's own count. 95 % is the T-030
     acceptance tolerance;
   - **excerpt:** `more_offered` is not empty;
   - **notice types:** at least 2 tenders, and at least 10 % of those delivered, carry another notice type's label.

   Reads with `stats.limited` are not judged on completeness.
3. Like every check, a failed read is not stored. **Attach, repair and audit promote a template only after `check`
   passes**, so a partial read can't reach `serving`. A portal that only offers an excerpt ends `failed` with the
   sentence as its reason, unless the agent finds the full list.
4. The agent's dry run (`try_template`) reports the same problems for its list check, so the agent sees "excerpt" or
   "unfiltered notice types" before it submits a template. A template that skips the list's pagination therefore no
   longer gets a GOOD verdict.

## Consequences

- Thresholds are module constants (`TOTAL_TOLERANCE`, `OTHER_NOTICES_MIN`, `OTHER_NOTICES_RATIO`), listed in
  [validate/checks.md](../validate/checks.md).
- **False alarms are the risk.** The wording lists are conservative: "Alle" alone, "Mehr erfahren" and a call's
  description mentioning an earlier prior notice don't fire, and every base mock portal reads clean (a test guards
  this). A real portal that trips a check wrongly ends `failed` with a reason that quotes the evidence, so the cause is
  visible. Extend the lists, or narrow them, with a regression test.
- Proactive audits (T-037) can re-check the declared total against the live portal.
- A corrigendum of an open call isn't treated as another notice type: it is about an open tender.

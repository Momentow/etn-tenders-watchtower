# 0016: Template v2 implementation notes

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-033
- **Amends:** [0014](0014-template-format-v2.md) §2–3 (adds to it; nothing in it changes meaning)

## Context

Building the v2 executor (T-033) and hand-writing templates for the `app` mock portal and tenderned.nl showed gaps in
ADR 0014. The agent (T-034), completeness checks (T-036) and repair (T-037) depend on how these were filled.

## Decision

1. **`{{today}}` placeholder.** Real APIs filter open tenders by a date: tenderned.nl takes
   `sluitingsDatumVanaf=<date>`. A fixed date in a template would go stale. `{{today}}` is today's date in the
   template's time zone (`YYYY-MM-DD`) and may be used in any request string, next to `{{session.*}}`.
2. **Scope semantics.**
   - A record is kept when it meets every `include` condition and no `exclude` condition.
   - If a condition's source has no value, the record doesn't meet an `include` condition and isn't caught by an
     `exclude` one.
   - `open_by_deadline` drops records with a known past deadline and keeps records without one.
   - It's checked on the list when the deadline is an item field, and otherwise after the detail page.
3. **`list.kind: page` has `via: browser | http`** (default `browser`). `via: http` reads `entry` with the polite client
   and allows no `flow` and no click, load-more or scroll pagination. **`detail.kind: page` has
   `via: http | browser`** (default `http`, the cheap path). It keeps v1's `resolve` hops for http, which handles the
   summary-page trap, and `views` need `browser`.
4. **An `api` detail needs `page_url` or `fields.url`.** The API URL isn't the tender's page, and returning it as `url`
   would be a wrong value.
5. **JSON names.** In stored and agent-written templates the keys are `from` (capture), `in` (condition) and `json`
   (request body), as in ADR 0014's examples. The pipeline stores templates with `by_alias`. `items` and `total` of an
   api list accept a bare JSONPath string as shorthand.
6. **Site check.** "Same registrable domain" is approximated as the last two host labels, or three labels after a
   second-level suffix such as `co.uk` or `gov.pl`. IP addresses and single-label hosts are used as they are. Requests
   may also go to any site that a session step's own URL is on.
7. **Read session budget.** A read's browser session gets 900 s of active time and 3,300 s in all (`SessionBudget`),
   enough for a list plus browser detail pages and within the read job's 3,600 s timeout. A `load_more` that stops at
   `max_clicks` sets `stats.hit_max_pages`, which T-036 can treat as a partial read.
8. **Examples** live in `watchtower/engine/examples/v2/` (`load_example_v2`), so v1 consumers of `examples/*.json`, such
   as the agent's prompts, are unchanged.

## Consequences

- T-034 can write `{{today}}` filters and must give api details a `page_url`.
- T-036 gets `declared_total`, `scope_dropped` and `hit_max_pages`. On tenderned.nl the declared total (2,884) includes
  301 early-termination notices that scope drops, so the completeness check compares `declared_total` with
  `len(tenders) + scope_dropped`.
- The pipeline loads stored templates with `load_template`. The investigate and audit code (`agent/evidence.py`) still
  reads v1 fields, so it handles v2 templates only after T-037.

# 0011 — Agent toolkit: analysis modules, context inputs, verified suggestions

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-022

## Context
ADR 0002 asks for deterministic tools wherever something can be computed, and ADR 0010 gives the registry to add them
to. The scenarios (T-015 attach, T-016 investigate) are written in parallel and must know which inputs the tools need
from them, and what the tools promise.

## Decision

### Layout
- `watchtower/agent/analysis/`: pure functions on engine scopes (no I/O, no model): `discovery` (repeating blocks, link
  classes), `selectors` (verified CSS for groups and within records), `recognition` (label → field, dates, software
  fingerprints), `diff` (snapshots, reads).
- `watchtower/agent/pages.py`: the per-run page cache (`get_page`, also for rendered pages), `page_scope` (engine
  parse) and `probe_document`. Core and toolkit tools share it.
- `watchtower/agent/tools/{discovery,recognition,verification,repair}.py`: thin `@tool` wrappers. Repair tools are
  registered for `investigate` only; the rest for both scenarios.

### Inputs from the scenarios (`AgentContext`, all optional)
| Field | Set by | Used by |
|---|---|---|
| `template` | investigate: the template in use | `diff_structure` (default page), `diff_outputs` (default old) |
| `last_good_snapshot` | investigate: `PortalView.last_good_snapshot` | `diff_structure` |
| `known_templates` | both: promoted templates of other portals by `software` | `fingerprint_software`, `find_template_for_software` |

### What tools promise
- Every selector or snippet a tool suggests has been run on the live page with the engine's own parser and CSS.
- Pagination suggestions are proved by fetching the next page and finding new records.
- "Blocked" is decided by downloading a document, never by the presence of a widget.
- Tools never write state; they cost requests (politeness applies) and a bounded amount of output text.

### Prompt
`BASE_INSTRUCTIONS` tells the model to prefer these tools and use their suggestions as given.

## Consequences
- T-015/T-016 should fill `known_templates` (from the store, through the pipeline) and, for investigate, `template`
  and `last_good_snapshot`. Without them the tools still work, minus reuse and diffs.
- More tool schemas and outputs in the context: a run on an easy portal can cost more tokens than without the toolkit,
  in exchange for completeness on hard ones. T-099 measures it; trimming the method or tool set comes after.

# Decisions (ADRs)

Record anything other sessions depend on. Copy [0000-template.md](0000-template.md) to `NNNN-<slug>.md`, using the next free number.
Never edit an accepted ADR; supersede it with a new one.

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-tech-stack.md) | Tech stack | accepted | 2026-11-20 |
| [0002](0002-architecture.md) | Architecture, module boundaries and interfaces | accepted | 2026-11-20 |
| [0003](0003-template-format.md) | Template format | accepted | 2026-11-20 |
| [0004](0004-storage.md) | Storage: SQLite schema and `core.store` API | accepted | 2026-11-20 |
| [0005](0005-run-requests.md) | Requesting work: runs are created when work is queued | accepted | 2026-11-20 |
| [0006](0006-job-handlers-and-state-events.md) | Job handlers and state-machine events | accepted | 2026-11-20 |
| [0007](0007-date-parsing.md) | Date parsing beyond the template's formats (amends 0003) | accepted | 2026-11-20 |
| [0008](0008-read-validation.md) | Read validation: `PortalHistory` and the checks | accepted | 2026-11-20 |
| [0009](0009-pipeline-and-agent-outcomes.md) | Read pipeline and the agent scenario outcomes | accepted | 2026-11-20 |
| [0010](0010-agent-runtime-and-tools.md) | Agent runtime, tool registry and budgets | accepted | 2026-11-20 |
| [0011](0011-investigate-deterministic-first.md) | Investigate: deterministic checks before the model | accepted | 2026-11-20 |
| [0012](0012-agent-toolkit.md) | Agent toolkit: analysis modules, context inputs, verified suggestions | accepted | 2026-11-20 |
| [0013](0013-audit-finding-repair.md) | Repairing an audit finding (amends 0009) | accepted | 2026-11-21 |
| [0014](0014-template-format-v2.md) | Template format v2 and browser sessions (amends 0003) | accepted | 2026-11-21 |
| [0015](0015-agent-v2-explorer-and-tiers.md) | Agent v2: explorer and model tiers (amends 0010, 0012) | accepted | 2026-11-21 |
| [0016](0016-template-v2-implementation-notes.md) | Template v2 implementation notes: `{{today}}`, scope semantics, `via` (amends 0014) | accepted | 2026-11-21 |
| [0017](0017-model-usage-metering.md) | Model usage metering: collector, prices, `model_usage` table | accepted | 2026-11-21 |
| [0018](0018-completeness-checks.md) | Completeness checks: a partial read is never served (amends 0008) | accepted | 2026-11-21 |

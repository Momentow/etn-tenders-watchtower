# Tasks

Shared board for every human and Claude session. Protocol: [CLAUDE.md → TASKS.md protocol](CLAUDE.md#tasksmd-protocol).

**Statuses:** `todo` · `in-progress` · `review` (PR open) · `done` · `blocked`
**Claim:** set Status, Owner and Branch in a tiny commit pushed straight to `master`. Only claim a task once all its dependencies are `done`.
**Areas:** `core` · `attach` · `notice` · `repair` · `dashboard` · `docs-site` · `infra` · `test-portals` · `cost`

| ID | Title | Area | Status | Owner | Branch | Depends on | Refs |
|---|---|---|---|---|---|---|---|
| T-000 | Collaboration protocol & docs skeleton | docs-site | review | Momentow | docs/T-000-collab-protocol | — | [CLAUDE.md](CLAUDE.md) |
| T-001 | Build test-portal registry | test-portals | todo | | | T-000 | [registry](docs/portals/registry.md), [requirements](docs/spec/requirements.md) |
| T-002 | Choose tech stack (ADR) + fill CLAUDE.md stack section | core | todo | | | T-000 | [requirements](docs/spec/requirements.md), [decisions](docs/decisions/README.md) |
| T-099 | LLM usage metering & cost per portal (last MVP task) | cost | todo | | | all other MVP tasks | [model-usage](docs/cost/model-usage.md) |

> Concrete MVP tasks (API skeleton, state machine, attach, notice, repair, dashboard, docs site, Dockerfile) get added once T-002 is done.

---

### T-000 — Collaboration protocol & docs skeleton
**Goal:** Every session follows one protocol for git, tasks, docs and contracts.
**Acceptance:**
- [x] `CLAUDE.md` with the stack-agnostic protocol
- [x] `TASKS.md` board
- [x] `docs/` skeleton: spec, decisions, portal registry, cost placeholder
**Refs:** [CLAUDE.md](CLAUDE.md)
**Notes:** —

### T-001 — Build test-portal registry
**Goal:** Our own test set, as the brief asks for.
**Acceptance:**
- [ ] Each portal has a public tender list and ≥ 5 open tenders whose documents download without login
- [ ] Covers ≥ 4 countries and ≥ 3 different portal software packages
- [ ] Mixes server-rendered pages, JS apps with a JSON API, and multi-step downloads
- [ ] 1–2 portals that need a login or captcha (to test that we report the block)
- [ ] A few portals marked `unseen`, held back for the final test
**Refs:** [docs/portals/registry.md](docs/portals/registry.md), [requirements](docs/spec/requirements.md#finding-test-portals)
**Notes:** —

### T-002 — Choose tech stack
**Goal:** Pick the language, framework, storage, scraping/browser approach, LLM provider and docs-site generator.
**Acceptance:**
- [ ] ADR `0001-tech-stack.md` accepted
- [ ] The "Tech stack, commands, architecture" section of CLAUDE.md is filled in
- [ ] Concrete MVP tasks added to this board, with dependencies
**Refs:** [requirements](docs/spec/requirements.md), [api-contract](docs/spec/api-contract.md)
**Notes:** —

### T-099 — LLM usage metering & cost per portal
**Goal:** Measure tokens and cost per portal (attach, check, repair) and report it in the README, as the "Approach" judging criterion asks.
**Acceptance:**
- [ ] Model usage recorded per portal and per run
- [ ] `docs/cost/model-usage.md` and the README state the cost of one portal
**Refs:** [docs/cost/model-usage.md](docs/cost/model-usage.md)
**Notes:** Deliberately last. Until then, keep all model calls behind one module.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Watchtower**: our entry for the Patterno 24-hour hackathon. Given only a procurement portal's URL, the system must
**Attach** (deliver every open tender plus its documents), **Notice** (report truthfully when delivery breaks, or can't be
repaired) and **Repair** (get back to correct delivery after the portal changes), all without human help. A
**dashboard** shows operators which portals deliver, which broke and what the system did about it.

- Requirements and judging criteria: [docs/spec/requirements.md](docs/spec/requirements.md)
- Exact API contract (authoritative): [docs/spec/api-contract.md](docs/spec/api-contract.md)
- Original brief: `docs/Patterno-Track-Description.pdf`

Several teammates run Claude Code sessions in parallel. **Everything below is a shared protocol. Follow it exactly**, so
sessions don't collide.

## Hard constraints (never violate)

- **One container, port 8080**: the API, the dashboard at `GET /` and the docs site at `/docs`. `docker build .` must work on
  `linux/amd64` and build fast on modest machines. The default command starts everything.
- **No steering.** No person or service of ours may direct the system during a run. All behaviour is autonomous.
- **Politeness:** at most 1 request/second **per portal** (across all workers), no logins, no captcha solving. A portal
  blocked by a login or captcha → state `failed`, and `reason` says so.
- **LLM usage:** the LLM learns how to read a portal, checks the result and repairs it. **Regular reads run without a model call.**
- **Secrets:** model keys come from environment variables only. Every variable is listed in the README.
- **The API contract is law.** Code against `docs/spec/api-contract.md`. Change it only via an ADR plus a TASKS.md entry.
- **A wrong value is worse than a missing one.** Send `null` for a required field the portal doesn't show, and leave out optional ones.
- **Known traps:** notice links that land on a summary page instead of the tender; deadlines shown in local time (always output
  an ISO 8601 offset); ZIP files over 1 GB (unpack ZIPs; files over 250 MB may be skipped); captcha pages whose documents
  still download without it.
- While a portal is `healing`, keep serving its last correct tenders.

## Session start checklist

1. `git fetch` and `git switch master && git pull --ff-only`.
2. Read [TASKS.md](TASKS.md). Pick a `todo` task whose dependencies are all `done`.
3. Claim it on master (see [TASKS.md protocol](#tasksmd-protocol)).
4. Create the feature branch (see [Git protocol](#git-protocol)).
5. Read every doc and ADR listed in the task's **Refs** before writing code.

## Git protocol

Plain `git` only; we don't use `gh`. Claude does all the branching, committing, rebasing and pushing. **The user opens and
merges PRs on the GitHub web UI.** Remote: `git@github.com:Momentow/etn-tenders-watchtower.git`.

- **Never commit to `master`**, with one exception: TASKS.md status edits.
- **Branch** from fresh master: `git fetch && git switch -c <type>/<task-id>-<slug> --no-track origin/master`.
  Types: `feat`, `fix`, `docs`, `chore`, `refactor`. Example: `feat/T-014-portal-state-machine`.
- **Commit after every working step.** Use Conventional Commits with the area as scope and the task ID in the body:
  `feat(attach): detect JSON list endpoint` + `Task: T-014`. Each commit should leave the code in a working state.
- **Push in batches**, not after every commit: before the PR, and a few times along the way on large features.
  Before **every** push:
  ```
  git fetch && git rebase origin/master
  git push --force-with-lease -u origin HEAD
  ```
  Force-push only your **own** feature branch, and always with `--force-with-lease`.
- **Conflicts during a rebase:** resolve trivial ones (imports, adjacent edits) yourself. When teammates' logic collides,
  stop, `git rebase --abort` if needed, and ask the user.
- **Task ready:** push, set the task to `review`, then give the user:
  - the compare URL: `https://github.com/Momentow/etn-tenders-watchtower/compare/master...<branch>`
  - a ready-to-paste PR title (`T-014: <title>`) and description: what changed, which docs were updated, how it was verified.
- **After the user merges:** `git switch master && git pull --ff-only`, `git branch -d <branch>`, set the task to `done`.
- **Never:** push to or force-push `master` (except TASKS.md edits), rewrite other people's branches, `reset --hard` or
  `checkout .` over uncommitted work, or commit `.env` files, keys or large downloaded portal documents.

## TASKS.md protocol

[TASKS.md](TASKS.md) is the single shared board. Humans read the table; Claude reads the table and the per-task sections.

- **Status edits are the only direct commits to `master`.** Keep them tiny and push immediately so other sessions see them:
  ```
  git stash -u   # only if the working tree is dirty
  git switch master && git pull --rebase
  # edit exactly the row(s) concerned
  git commit -am "chore(tasks): claim T-014" && git push
  git switch - && git stash pop   # back to work
  ```
  If the push is rejected, run `git pull --rebase` and push again. Never overwrite another person's row.
- **Statuses:** `todo` → `in-progress` → `review` (PR open) → `done`; or `blocked` (give the reason in the task's notes).
- **Claiming** means setting `Status=in-progress`, `Owner=<git user.name>`, `Branch=<branch>`. Never take a task someone else owns.
- New tasks, splits and progress notes also go into TASKS.md via direct commits. Use the next free `T-NNN` ID.
- A task's **Refs** point to the spec, ADRs and docs that define it. If the definition is missing, write it into the docs first.

## Development approach

- **Extend the codebase; don't make major edits to existing code, because they can break other people's work.** Add new
  modules, functions or files next to existing ones. If a shared interface really has to change, write an ADR first and
  add a TASKS.md entry so the owners of the code that uses it know.
- Code against the contracts in `docs/spec/` and merged code on `master`, **never** against another session's unmerged branch.
- Keep each task inside its area. A needed change to shared code gets its own small commit with an explicit message, or its own task.
- Build the simplest thing that meets the task's acceptance criteria. No speculative features.
- **LLM cost metering is the last MVP task.** Don't build usage tracking early. Just keep model calls in one place so they can be measured later.

## Documentation rules

- Markdown in `docs/` is the **single source** (English). The container serves it as a static site at `/docs`, styled as
  modern, minimal product docs. The site is generated from these files; never hand-edit generated output.
- **A new page goes into `nav` in `mkdocs.yml`** (a test enforces it). Details: [docs/docs-site/docs-site.md](docs/docs-site/docs-site.md).
- **Every feature PR updates the relevant docs in the same branch.** A PR with no docs change states why in its description.
- Which doc owns what:

  | Path | Owns |
  |---|---|
  | `docs/index.md` | Docs home and navigation |
  | `docs/spec/requirements.md` | What we must build and how we're judged |
  | `docs/spec/api-contract.md` | Exact HTTP API, portal states, tender fields |
  | `docs/decisions/` | ADRs: cross-module and architecture decisions |
  | `docs/portals/registry.md` | Our test-portal set and each portal's quirks |
  | `docs/test-portals/mock-portals.md` | Mock portals, their variants and how to use them in tests |
  | `docs/cost/model-usage.md` | LLM usage and cost per portal |
  | `docs/<area>/` | How each module works (added along with the module) |

- `README.md` stays a single page covering how the system works, the env vars it needs, and the model cost per portal
  (the brief requires this).

## Decisions (ADRs)

Write an ADR in `docs/decisions/NNNN-<slug>.md` (copy [0000-template.md](docs/decisions/0000-template.md)) for anything
other sessions depend on: the tech stack, module boundaries and interfaces, the portal state machine, storage, the LLM
usage strategy, and any API contract clarification. Add it to the index in `docs/decisions/README.md` and commit it on the
task's branch. If an accepted ADR changes, write a new ADR that supersedes it; don't edit the old one.

## Tech stack

Python 3.12+, asyncio throughout · FastAPI + uvicorn · httpx · selectolax / lxml / jsonpath-ng · Playwright (Chromium) ·
SQLite via aiosqlite · OpenAI Agents SDK · Jinja2 + htmx dashboard · Zensical docs site · uv · ruff · pytest.
Rationale and rejected options: [ADR 0001](docs/decisions/0001-tech-stack.md).

Env vars (full table in ADR 0001): `OPENAI_API_KEY` (required), `WATCHTOWER_MODEL`, `DATA_DIR` (default `/data`),
`WATCHTOWER_HEALTHCHECK_MODE` (`reactive` | `proactive`, default `proactive`), `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` (default `24`).
A new env var is added to the ADR 0001 table and the README in the same PR.

## Architecture

Full boundaries, interfaces and the portal lifecycle: [ADR 0002](docs/decisions/0002-architecture.md). Read it before touching a module.

- **Static engine, agent writes templates.** The `engine` reads every portal by executing a declarative template, with no
  model call. The `agent` runs only for `generate_template` (attach) and `investigate` (notice → repair), and outputs a
  template or a diagnosis, **never code**. The engine validates every template before it is promoted.
- **Single owners, never bypassed:**
  - all portal traffic → `net` (enforces ≤ 1 req/s per host);
  - all model calls → `agent`;
  - all `state`/`reason` changes → the state machine in `core`;
  - all DB access → `core.store`.
- Read pipeline: engine run → `validate.check` → upsert tenders and `lastChangedAt` → enqueue document downloads → record
  the run → state transition. A failed validation moves the portal to `healing` and enqueues `investigate`, while the last correct tenders keep being served.
- **Health checks:** `reactive` investigates when a read fails. `proactive` does that too, and also audits every serving
  portal every N hours ([ADR 0002 → Health-check modes](docs/decisions/0002-architecture.md#health-check-modes)).
- **Deterministic first.** If the agent needs something that can be computed (list detection, pagination, date formats,
  software fingerprint, selector tests…), add or extend a tool in the agent toolkit rather than prompting the model to work it out.
- Everything runs in one process: API, dashboard, `/docs`, scheduler and workers. Jobs are persisted in SQLite. Files are streamed to `$DATA_DIR/files/<sha256>`.
- `mockportals/` holds local fake portals plus mutated variants. Use them to test Attach, Notice and Repair offline.

## Commands

Install `uv` (https://docs.astral.sh/uv/). Python 3.12 is pinned in `.python-version`; uv fetches it if needed.

```
uv sync                                         # install deps (incl. dev group)
uv run python -m watchtower                     # start the service on :8080
uv run python -m mockportals                    # mock portals on :8081 (docs/test-portals/mock-portals.md)
uv run pytest                                   # all tests
uv run pytest tests/test_smoke.py::test_index_serves_placeholder_page   # single test
uv run ruff check . && uv run ruff format .     # lint + format
uv run --group docs zensical build              # docs site → site/, served at /docs
docker build -t watchtower . && docker run -p 8080:8080 -e OPENAI_API_KEY watchtower
```

The `playwright` pin in `pyproject.toml` must match the `mcr.microsoft.com/playwright/python` tag in the `Dockerfile`;
bump both together.

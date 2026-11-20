# Watchtower docs

Watchtower attaches to public-procurement portals on its own, watches over them, and repairs itself when one breaks.

## Specification
- [Requirements](spec/requirements.md): what we build and how it's judged
- [API contract](spec/api-contract.md): endpoints, portal states, tender fields

## Engineering
- [Decisions (ADRs)](decisions/README.md): [tech stack](decisions/0001-tech-stack.md), [architecture](decisions/0002-architecture.md), [template format](decisions/0003-template-format.md), [storage](decisions/0004-storage.md), [run requests](decisions/0005-run-requests.md), [job handlers](decisions/0006-job-handlers-and-state-events.md), [date parsing](decisions/0007-date-parsing.md), [read validation](decisions/0008-read-validation.md), [pipeline outcomes](decisions/0009-pipeline-and-agent-outcomes.md), [agent runtime](decisions/0010-agent-runtime-and-tools.md), [investigate](decisions/0011-investigate-deterministic-first.md)
- [HTTP API](api/http-api.md): implementation notes on the contract
- [Store](core/store.md): the database layer
- [Scheduler and state machine](core/scheduler.md): workers, periodic reads, audits, portal states
- [Read pipeline](core/pipeline.md): attach, read, repair and audit flows, how tenders are stored
- [Network layer](net/network.md): politeness, HTTP client, browser, block detection
- [Engine executor](engine/executor.md): how a template is run, errors, normalisation, snapshots
- [Read validation](validate/checks.md): when a read counts as broken, and the sentences it reports
- [Normalisers](engine/normalize.md): dates by locale, CPV, enums, countries, content hash
- [Document downloader](documents/downloader.md): streaming, ZIPs, the 250 MB rule, the download job
- [Dashboard](dashboard/dashboard.md): the operator pages at `/`, run timeline, live refresh
- [Agent runtime and tools](agent/runtime.md): model calls, budgets, the tool registry, pruned pages, dry runs
- [Investigate](repair/investigate.md): diagnosing failed reads, audits, repairs; deterministic checks first
- [Test-portal registry](portals/registry.md) · [Mock portals](test-portals/mock-portals.md)
- [Model usage & cost](cost/model-usage.md)
- [Docs site](docs-site/docs-site.md): how this site is built and served, and how to add a page

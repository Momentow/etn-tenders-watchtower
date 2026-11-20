# Watchtower docs

Watchtower attaches to public-procurement portals on its own, watches over them, and repairs itself when one breaks.

## Specification
- [Requirements](spec/requirements.md): what we build and how it's judged
- [API contract](spec/api-contract.md): endpoints, portal states, tender fields

## Engineering
- [Decisions (ADRs)](decisions/README.md): [tech stack](decisions/0001-tech-stack.md), [architecture](decisions/0002-architecture.md), [template format](decisions/0003-template-format.md), [storage](decisions/0004-storage.md), [run requests](decisions/0005-run-requests.md), [job handlers](decisions/0006-job-handlers-and-state-events.md)
- [HTTP API](api/http-api.md): implementation notes on the contract
- [Store](core/store.md): the database layer
- [Scheduler and state machine](core/scheduler.md): workers, periodic reads, audits, portal states
- [Network layer](net/network.md): politeness, HTTP client, browser, block detection
- [Test-portal registry](portals/registry.md) · [Mock portals](test-portals/mock-portals.md)
- [Model usage & cost](cost/model-usage.md)

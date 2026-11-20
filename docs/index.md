# Watchtower docs

Watchtower attaches to public-procurement portals on its own, watches over them, and repairs itself when one breaks.

## Specification
- [Requirements](spec/requirements.md): what we build and how it's judged
- [API contract](spec/api-contract.md): endpoints, portal states, tender fields

## Engineering
- [Decisions (ADRs)](decisions/README.md): [tech stack](decisions/0001-tech-stack.md), [architecture](decisions/0002-architecture.md), [template format](decisions/0003-template-format.md), [storage](decisions/0004-storage.md)
- [Store](core/store.md): the database layer
- [Network layer](net/network.md): politeness, HTTP client, browser, block detection
- [Test-portal registry](portals/registry.md)
- [Model usage & cost](cost/model-usage.md)

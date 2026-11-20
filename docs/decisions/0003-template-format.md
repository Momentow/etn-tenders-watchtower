# 0003 — Template format

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-008

## Context
ADR 0002 splits reading a portal into an **agent** that learns how to read it and an **engine** that reads it on every
run with no model call. The template is the contract between them: the agent writes one (and repairs it), the engine
executes it, the store versions it. It has to describe server-rendered lists, JSON APIs found behind JS apps, search forms,
detail pages, multi-step downloads, local-time dates and status labels, without being code.

## Decision

A template is a pydantic model, `watchtower.engine.template.Template`, stored as JSON. Its JSON schema
(`template_json_schema()`, checked in at [`docs/engine/template.schema.json`](../engine/template.schema.json)) is the
agent's structured-output type. Example templates for every mock-portal kind are in `watchtower/engine/examples/`.

### Shape

```
Template
├── format_version: 1
├── timezone          IANA zone for times shown without an offset (Europe/Berlin)
├── country?          ISO alpha-2 of the portal
├── software?         portal-software fingerprint, for template reuse
├── list              where the tenders are listed (one of):
│   ├── kind: html    request, steps[], items (css/xpath, one match per tender), pagination, render
│   └── kind: json    request, items (jsonpath to the tender objects), pagination
├── detail?           one page per tender: link (on the item) | url_template ("…/{id}"),
│                     format html|json, render, resolve[] (summary page → real tender page)
├── fields            contract tender fields by their contract names; id required, the rest optional
├── documents?        on item|detail; links[], names[], bundle ("download all"), include/exclude, per_link[] hops
├── status?           value + regexes for withdrawn / closed
└── notes             free text from the agent
```

### Extraction (`Extract`)
One shape everywhere: `{by, expr, attr, many, pattern, map, join}`.
- `by`: `css`, `xpath` (HTML), `jsonpath` (JSON), `regex` (either), `const` (a fixed value).
- `attr` (css/xpath only): `text` (default), `html`, or an attribute name such as `href`. Relative URLs are made absolute.
- Post-processing, in order: `pattern` (regex, group 1), `map` (portal label → contract value; unmapped is dropped),
  `join` (for `many`). Dropping is deliberate: **a missing value is better than a wrong one.**
- Field rules add `on: item | detail`. Date rules add `formats` (strptime patterns; none = ISO 8601).

**Scopes:** item fields run against one list item (an HTML element or a JSON object), detail fields against the detail
page. HTML scopes take css/xpath/regex/const; JSON scopes take jsonpath/regex/const.

### Navigation
- `steps` on an HTML list: `follow` (go to a link) and `submit` (send a form, keeping its hidden inputs, e.g.
  `__VIEWSTATE`, with `values` overridden). This covers search forms and landing pages.
- `detail.resolve`: `follow` hops from a notice/summary page to the real tender page (a known trap).
- `documents.per_link`: `follow` hops from each document link through download pages to the file.

### Pagination
`none`, `next_link` (a link or a next-URL in the JSON), `param` (page number or offset in the query, form or JSON body),
`cursor` (JSON APIs). All stop on an empty page or a page with no new ids, and at `max_pages` (default 50, max 500).

### Browser
`list.render` / `detail.render` load pages through `BrowserPool`. A JSON API discovered in the browser's captured XHR
should become a `json` list instead: it's read directly over HTTP, which is cheaper and more stable.

### Status
No status rule or no match → `open`. `withdrawn` / `closed` regexes are case-insensitive. The pipeline still closes
tenders past their deadline or no longer listed (ADR 0002).

### Validation at load time
`Template.model_validate` rejects a template before it's ever run when:
- an expression doesn't compile (CSS, XPath, JSONPath, regex);
- an extractor can't read its scope (css on JSON…);
- a detail-page field, document or status rule has no `detail`;
- `fields.id` isn't on the item, or `url` has no source;
- `url_template` uses a field the item doesn't provide;
- pagination doesn't fit the request;
- there are credential headers (`Authorization`, `Cookie`): we never log in;
- the time zone is unknown;
- there are unknown keys (typos).

Each error names the path, so the agent can fix it. Whether a template actually *reads the portal correctly* is
checked afterwards by an engine dry run and `validate.check` (T-009, T-011).

## Consequences
- The engine (T-009) implements exactly these semantics. Normalisers (T-010) receive raw strings plus `formats`,
  `timezone` and `country`.
- The agent (T-014/T-015/T-016) outputs `Template` and can be shown the examples as few-shot references.
  The schema uses `oneOf` with discriminators and optional fields, so use the Agents SDK with
  `strict_json_schema=False` (or a strict-compatible wrapper) for structured output.
- The examples point at mock portals on `http://localhost:8081/<kind>/…`. T-013 should build the mocks to match them,
  or update the examples in the same PR.
- New capabilities (another pagination kind, a new step action) are added as new union members with an updated
  schema file. Changing the meaning of an existing key needs a new ADR and `format_version: 2`.

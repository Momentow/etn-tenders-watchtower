# Template format v2 and its executor

Format v2 ([ADR 0014](../decisions/0014-template-format-v2.md), additions in
[ADR 0016](../decisions/0016-template-v2-implementation-notes.md)) describes portals the way a person uses them: open a
page, click through, load more, open a tender, switch to a tab. It also lets a template read the JSON API behind those
pages directly, with no browser. v1 templates ([ADR 0003](../decisions/0003-template-format.md)) keep running unchanged.

```python
from watchtower.engine import load_template, run_template

template = load_template(stored_json)          # Template (v1) or TemplateV2, by `format_version`
result = await run_template(template, client, browser, limit=None)
result.declared_total   # the portal's own count (`list.total`), v2 only
result.scope_dropped    # records read but not open tenders in `scope`, v2 only
result.more_offered     # both formats: an unpaginated list page's signs of more (completeness, ADR 0018)
result.other_notices    # both formats: delivered tenders labelled as another notice type (ADR 0018)
```

- Code: `watchtower/engine/template_v2.py` (schema and load-time checks) and `watchtower/engine/executor_v2.py`
  (`run_template` hands v2 templates to it).
- JSON schema: [`template-v2.schema.json`](template-v2.schema.json), regenerated with
  `uv run python -m watchtower.engine.template_v2 > docs/engine/template-v2.schema.json` (a test checks it).
- Examples: `watchtower/engine/examples/v2/` (`load_example_v2(name)`):

| Example | Portal | Shape |
|---|---|---|
| `app_api` | mock `app` | `list.kind: api` with the API's own filters, `detail.kind: api`, documents from an API call. No browser |
| `app_click` | mock `app` | `list.kind: page`: home → click *Alle aankondigingen* → *Laad meer* until gone; detail pages in the browser with a *Documenten* tab view |
| `tenderned` | [tenderned.nl](https://www.tenderned.nl) | API mode, hand-written from a probe; reads every open call for competition |

## Shape

```
format_version: 2
timezone, country?, software?, notes
entry.url                 where reading starts (may be deeper than the portal URL)
session?                  via http|browser, steps, capture[{name, from, match, key}]
list                      kind: api   request, items (jsonpath), total?, pagination none|param|cursor|next_link
                          kind: page  via browser|http, flow [actions], items (css/xpath) + group?, total?,
                                      pagination none|next_link|param|click|load_more|scroll
detail?                   kind: api   request ({field} placeholders), page_url
                          kind: page  link | url_template, via http|browser, views {name: [actions]}, resolve (http)
fields                    as v1; `on`: item | detail | detail.<view>
documents?                as v1, plus `on: detail.<view>` and `request` (a JSON call returning the file list)
status?                   as v1
scope                     include [Condition], exclude [Condition], open_by_deadline (default true)
urls                      drop_params [], drop_matrix ["jsessionid"], drop_numeric_query false
```

Actions are the browser session's actions ([network layer](../net/network.md)): `goto`, `click`, `fill`, `select`,
`press`, `wait_for`, `load_more`, `scroll` and `tab`, with targets given as role and name, text, css or xpath. There is no
action that runs JavaScript.

### Placeholders

| Placeholder | Where | Value |
|---|---|---|
| `{{session.<name>}}` | any request string | a value the `session` captured on this read; never stored or logged |
| `{{today}}` | any request string | today's date in the template's time zone, `YYYY-MM-DD` (e.g. `sluitingsDatumVanaf`) |
| `{field}` | detail and document requests, `page_url`, `url_template` | an item field's value (URL-quoted in URLs) |

A `Cookie` or `Authorization` header is allowed only when its value is a `{{session.*}}` reference.

### Scope

A record is kept when it meets **every** `include` condition and **no** `exclude` condition. A condition is
`{source, in | matches | equals}`. `source` is read from the list item, and values compare as text, so JSON `true`
compares as `true`. If the source has no value, the record doesn't meet an `include` condition and isn't caught by an
`exclude` one. With `open_by_deadline`, a record whose deadline has passed is dropped. This is checked on the list
when the deadline is an item field, and after the detail page when it's read there. A record without a deadline is
kept. Dropped records are counted in `scope_dropped` and never returned.

Use the API's own filters first, as query parameters on `list.request`, and `scope` for what they miss. On tenderned.nl,
`publicatieType=AAO` still returns early-termination notices, and `scope` removes them.

### Load-time checks

Everything v1 checks, plus these:
- Every action and target is well-formed.
- Every `{{session.x}}` names a declared capture, and `{field}` placeholders use item fields.
- `detail.<view>` names a declared view, and views need `detail.via: browser`.
- `flow`, `click`, `load_more` and `scroll` need `list.via: browser`.
- Extractors fit their scope: JSON for `api`, HTML for `page`.
- An `api` detail has `page_url` or `fields.url`, because the API URL isn't the tender's page.
- Every request stays on the entry's registrable domain (`www.tenderned.nl` → `tenderned.nl`) or on a site the session's
  own steps open.

## What a run does

1. **Session** (if any): `via: http` sends the steps with the polite client and captures from response headers, JSON
   or cookies. `via: browser` runs the actions in the read's browser session and captures from the headers the page's
   own requests carried, their JSON, or cookies. If the session fails, the read stops with
   `Getting the portal's anonymous session failed: …`.
2. **List**:
   - `api`: pages over HTTP exactly like a v1 JSON list.
   - `page` with `via: browser`: open `entry`, run `flow`, then paginate:
     - `load_more` clicks until the control is gone, disabled or brings nothing new, then reads the page once. If it
       stops at `max_clicks`, `stats.hit_max_pages` is set.
     - `click` reads a page, clicks "next" and repeats.
     - `next_link` and `param` navigate.
   - `page` with `via: http`: fetch `entry` and paginate like v1.

   On the first page, `total` gives `declared_total`. With `items.group`, sibling matches are merged into one scope per
   tender, either every `size` elements or from each `starts_at` match to the next. Each item is checked against `scope`.
3. **Per tender**: the detail. For `api`, a JSON call. For `page` over `http`, the page plus its `resolve` hops. For
   `page` in the browser, `goto`, then each view's actions, each view's page becoming the `detail.<view>` scope. Then
   fields, documents (from their scope, or from `documents.request`), status and normalisation. The tender `url` is
   `fields.url`, else `page_url` (api) or the detail page's URL, with the `urls` normalisers applied, as they are to
   document links.
4. **`open_by_deadline`** on the normalised deadline.

One browser session at most per read, opened only when the template needs one. Its budget allows 900 s of active time
and 3,300 s in all; time spent waiting for the rate limiter doesn't count. Every request, browser sub-requests
included, still waits for the shared per-host limiter. Failures become plain-sentence `errors`, as in v1. A failed
`flow` step or view action is reported with the action, e.g. ``list.flow step 1: click link 'Alle aankondigingen'
failed (nothing on the page matches …)``.

## Cost

| Template | Requests per read |
|---|---|
| `tenderned` (live, 2026-11-21) | 59 list calls (2,884 records, 50 per page) + 1 documents call per open call (2,582). List alone ~60 s at 1 req/s; with documents ~45 min |
| `app_api` (mock) | 4 list + 21 detail + 21 document calls |
| `app_click` (mock) | one browser session: the boot, 2 *Laad meer* clicks, then per tender a page load and a tab click (~67 requests) |

API mode is the cheap path. `kind: page` with browser detail pages is the slowest, and the agent should use it only when
there's no usable API ([ADR 0015](../decisions/0015-agent-v2-explorer-and-tiers.md)).

## Tests

- `tests/engine/test_template_v2.py`: the examples load and round-trip through the pipeline's storage form, `load_template`
  picks the format, and each load-time check rejects what it should.
- `tests/engine/test_executor_v2.py`:
  - Both `app` templates read every open call exactly, compared with `expected_tenders("app")`: fields, documents,
    `declared_total` and `scope_dropped`.
  - The `api-moved` and `relabelled` variants break them with a plain reason.
  - Browser and HTTP sessions, row groups, scope, `open_by_deadline` (on the item and on the detail), URL normalisers
    and `{{today}}`.
  - The browser tests are skipped without Chromium.

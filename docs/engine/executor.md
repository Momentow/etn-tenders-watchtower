# Engine executor

`watchtower.engine.run_template` reads a portal by executing its [template](../decisions/0003-template-format.md). It
makes **no model call and no database access**: it takes a template, the shared `PoliteClient` and (if the template
renders pages) the `BrowserPool`, and returns a `ReadResult` ([ADR 0002](../decisions/0002-architecture.md#interfaces-stable-changing-them-requires-a-new-adr)).

```python
from watchtower.engine import run_template

result = await run_template(template, client, browser, limit=None)
result.tenders    # list[TenderRecord]: contract-shaped, documents as DocumentRef(url, fileName?)
result.errors     # plain sentences; empty = nothing went wrong
result.stats      # pages, requests, durations, duplicate ids, max_pages hit
result.snapshot   # structure of the first list page, for repair diffs
```

## What a run does

1. **List request**, then the navigation `steps`: `follow` a link, or `submit` a form (its hidden inputs such as
   `__VIEWSTATE` are kept, `values` override fields; GET forms become query parameters, POST forms a form body).
2. **List pages** with the template's pagination. Every kind stops on an empty page, on a page with no new ids, and at
   `max_pages` (then `stats.hit_max_pages` is set). `next_link` also stops when the link points to a page already read.
   Item fields (`on: item`) are read here; an item without an `id` is skipped and reported.
3. **Per tender:** the detail page (`link` on the item or `url_template` filled from URL-quoted item fields), then
   the `resolve` hops (summary page → real tender page). The final page's URL is the tender `url` unless
   `fields.url` is given. Detail fields, documents and status are read from their scope.
4. **Documents:** the `bundle` link if there is one, otherwise `links` with `names` in the same order, filtered by
   `include`/`exclude` (on URL or name), then the `per_link` hops. A hop target that isn't HTML is taken as the file
   itself (its body is not downloaded). The engine only returns URLs; downloading is `documents` (T-012).
5. **Status:** the `status.value` label against the `withdrawn` / `closed` regexes; otherwise `open`.
6. **Normalisation** into contract values (below).

`limit` stops after that many tenders, before their detail pages are fetched: the agent's dry run.

## Extraction

HTML is parsed with lexbor (HTML5, like a browser: `<tbody>` is inserted) and handed to lxml, so CSS (cssselect) and
XPath run on one tree and a template may mix them. CSS selectors also match the scope element itself, so
`{"by": "css", "expr": "article.x", "attr": "data-id"}` works on an `article.x` item. `href`/`src` values and every
URL the engine follows are made absolute; `javascript:`, `mailto:` and `#` links are dropped. JSONPath values that
aren't strings become strings (`5` → `"5"`).

## Errors, never exceptions

Fetch failures (HTTP ≥ 400, timeouts, invalid JSON) and navigation failures become sentences in `errors`:

| Situation | Result |
|---|---|
| List page unreachable | no tenders, `Reading the tender list failed: GET … returned HTTP 410.` |
| List page shows no tenders and a login form or captcha | an error naming the evidence (from `net.detect_block`) |
| One detail page fails | the tender is still returned with its item fields, `complete=False`, plus `Tender X: …` |
| Template renders, no browser given | no tenders, an error |

Silent problems (changed markup that yields zero items, a pager that moved so only page 1 is read) produce **no**
error: they are found by `validate.check` (T-011), which compares the result with the portal's history.

## Normalisation (placeholder until T-010)

`engine/basic_normalize.py` holds a minimal version behind one entry point, `normalize_fields(raw, template)`, which
T-010 replaces:

- dates: `formats` (strptime) or ISO 8601; times without an offset are read in the template's `timezone`
  (DST-aware). `publicationDate` becomes a date. A `submissionDeadline` with no time of day becomes `null`
  (we can't state the time truthfully);
- `cpvCodes`: 8-digit codes, check digit stripped, duplicates removed;
- `procedureType` / `contractNature`: only contract values survive (use `map` in the template);
- `countryCode`: two letters, upper-cased; `;jsessionid=…` is removed from tender URLs.

## Snapshot

`result.snapshot` describes the first list page's structure without its text, to be stored with each good read and
diffed by repair (T-022 `diff_structure`):

- HTML: `{"kind": "html", "url", "outline"}`: an indented `tag#id.class` tree; runs of same-shaped siblings
  collapse to one line such as `tr ×10`;
- JSON: `{"kind": "json", "url", "shape"}`: the key/type shape, arrays reduced to their first element.

## Tests

`tests/engine/test_executor.py` runs every example template against its mock portal and compares the result with
`mockportals.data.expected_tenders()`, field by field. It also covers mutated variants, stubbed edge cases (pagination
loops, failed detail pages, JSON-body cursors) and the captcha portal through the browser (skipped without Chromium).

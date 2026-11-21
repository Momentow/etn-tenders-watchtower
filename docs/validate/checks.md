# Read validation (`watchtower.validate`)

`check(result, history)` decides whether a read can be trusted: the **Notice** signal. A read with any problem isn't
stored. The portal goes to `healing`, the last correct tenders keep being served, and `investigate` gets the problems
as its issue report. Decision record: [ADR 0008](../decisions/0008-read-validation.md).

```python
from watchtower.validate import PortalHistory, check

history = PortalHistory.from_store(await store.list_runs(pid), await store.list_tenders(pid))
health = check(result, history)        # Health(ok=False, problems=["Only 10 tenders were found; recent reads found 20. …"])
```

`check` is pure: no network, no database, no model. Every problem is one plain sentence with numbers, written for the
portal's `reason` and the dashboard.

## Checks

| Check | Fails when | Example sentence |
|---|---|---|
| Engine errors | the engine reported any error (fetch failure, login wall, failed detail page…); more than 3 are summarised | `Reading the tender list failed: GET … returned HTTP 410.` |
| No tenders | zero tenders were read | `The read found no tenders; recent reads found 20.` |
| Sharp drop | fewer than 60 % of the usual count (median of the last 5 good reads) **and** at least 3 fewer | `Only 10 tenders were found; recent reads found 20. Some tenders are probably being missed.` |
| Page limit | pagination stopped at `max_pages` | `The read stopped at the page limit after 50 list pages, so tenders may be missing.` |
| Duplicate ids | an id appears twice in the result | `1 tender id(s) appear more than once (a).` |
| Repeating pages | later pages returned ≥ max(3, half the tenders read) ids already seen | `The list pages repeat: 5 tenders came back again on later pages. …` |
| Field newly missing | a watched required field was shown on ≥ 50 % of served tenders and now on at most half that share | `submissionDeadline is missing on 4 of 5 tenders; before, it was shown on 100% of them.` |
| First read without titles | no history and more than half the tenders have no title | `title is missing on 5 of 5 tenders.` |
| Invalid dates | `publicationDate` isn't an ISO date, or `submissionDeadline` isn't an ISO date-time with an offset | `submissionDeadline is not a date-time with an offset on 1 tender(s) (c).` |
| Implausible dates | publication more than a day in the future, or a deadline before publication | `submissionDeadline is before publicationDate on 1 tender(s) (d); the dates are probably misread.` |
| Deadlines jump | ≥ 3 known tenders and more than half of them changed deadline at once ([ADR 0007](../decisions/0007-date-parsing.md)) | `The deadline changed on 6 of 6 known tenders at once (…); the date format has probably changed.` |
| Below the portal's total | the portal's own count (`list.total`, v2) is known and tenders delivered + dropped by scope < 95 % of it | `The portal says its list holds 22, but the read reached only 10. Tenders are being missed.` |
| Excerpt | a list read without pagination whose page offers more: a next or numbered page control, an "all tenders" / "load more" link, a JSON total larger than the page | `The read took 20 tenders from a single list page, but that page offers more (a link 'Alle Bekanntmachungen' to …). It is probably a short excerpt such as a 'latest tenders' box, not the full list.` |
| Other notice types | ≥ 2 tenders, and ≥ 10 % of them, are labelled as awards, prior information, market consultations, intended tenders or early terminations | `7 of 20 tenders are other notice types, not calls for competition (e.g. 1138697: '…Vergebener Auftrag…'). The list's notice types need filtering.` |

The last three are the completeness checks (T-036, [ADR 0018](../decisions/0018-completeness-checks.md)). They judge
a read against the portal itself rather than against history, using evidence the engine records on the read
(`declared_total`, `scope_dropped`, `more_offered`, `other_notices`). A dry run stopped at a `limit` isn't judged on
them. The agent's `try_template` reports them for its list check too.

Normal changes pass: a few tenders more or fewer, one extended deadline, a field the portal never showed, the same
deadline written with another offset.

## What it does not catch
Values that still look right but aren't, e.g. the buyer column now showing the city (`silent-swap`), or new tenders
listed somewhere the template doesn't read (`silent-missed`), or a list capped below the portal's own hit count when
the drop is under the sharp-drop threshold (`silent-cut`). The proactive audit (T-023, T-041) compares engine output
with the live portal for those.

## Tests
`tests/validate/test_completeness.py`:
- The evergabe.nrw.de regression: a fixture with the real home page's structure, with the latest-20 box and an
  "Alle Bekanntmachungen" link. It fails as an excerpt mixing award notices, and attaching it ends `failed`, never
  `serving`.
- The `app` mock read without its notice-type filter or as its first API page only, and the `js` API without
  pagination: each ends `failed` with its reason.
- Complete reads are served, and every base mock read carries no completeness evidence.
- Thresholds, and the wording that must and must not count as evidence.

`tests/validate/test_health.py` runs the real engine on the mock portals: base reads pass, and the `pagination`,
`markup`, `renamed-keys`, `login-wall` and `gone` variants each fail with the expected sentence. Unit cases cover
every threshold, and one test builds the history from a real `Store`.

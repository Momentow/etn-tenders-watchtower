# Dashboard

The page an operator opens in the morning: every portal, whether it delivers, what broke, and what the system did
about it. Code: `watchtower/dashboard/` (T-018).

## Pages

| Path | Shows | Live refresh |
|---|---|---|
| `GET /` | Counts per state and total tenders served; one row per portal with state, reason, tender count, last success, last activity and **model cost** (with a total tile; see [model usage](../cost/model-usage.md)). Problems sort first: `failed`, `healing`, `integrating`, then `serving`. | every 5 s |
| `GET /dashboard/portals/{id}` | Portal header (state, reason, current template version, activity), the **run timeline**, **model usage** (calls, tokens and cost per scenario, tier and model), **template versions** and **tenders** with their documents. Unknown or deleted id → 404 page. | header and timeline every 5 s; templates and tenders every 30 s |
| `GET /dashboard/portals/{id}/templates/{version}.json` | One template version as JSON. | — |

The `/dashboard/partials/...` routes return the HTML fragments that htmx swaps in on each poll. They aren't part of the
API contract.

## Run timeline

Each run is labelled from its kind and `details.trigger` (ADR 0005):

| Run | Label |
|---|---|
| `read` + `attach` | Attach |
| `read` + `scheduled` | Scheduled read |
| `read` + `refresh` | Refresh requested |
| `read` + `audit` | Health audit (an audit is recorded as a `read` run, ADR 0002) |
| `repair` + `audit_finding` | Repair after audit |
| `repair` | Repair |

The "What happened" column renders these `details` keys when present:

- `problems`: a list of plain sentences, as written by `validate.check` (ADR 0008);
- `error`: an exception message from the scheduler;
- `cancelled`: shown as "Cancelled: …" (deleted portal);
- `diagnosis` + `diagnosis_reason` (written by the pipeline): shown as `Healthy: …`, `Changed: …`;
- `tenders` (`{new, changed, closed, …}`): shown as "2 new, 1 closed";
- `diagnosis`: a string, or a dict with `kind` and `reason`/`notes` (for example `{"kind": "Changed", "reason": "…"}`),
  shown as `Changed: …`.

`audit`, `audit_finding`, `stats` and `template_version` (shown in the Template column) aren't listed. Any other key is listed as a short key/value line, so new pipeline details show up without dashboard changes. Writers
of run details (T-017, T-016, T-023) should prefer these keys.

## Health checks
The overview states the health-check mode (reactive / proactive, with the interval) and has a **Last audit** column:
the last audit's result (healthy / found a problem), when it ran and when the next one is due (from
`Scheduler.audit_schedule()`). The portal page shows the same in its header. See [health checks](../notice/health-checks.md).

## Last activity

One line per portal: `Attaching now`, `Repairing now` or `<run> running` while a run is open; `Repair queued` while
`healing` with no run open; `Repaired` / `Repair failed` when a repair was the last finished run; otherwise
`<run> ok` / `<run> failed`.

## Rules

- **Read-only.** The dashboard only reads through `core.store`. It has no buttons that start work; operators use the
  API. No steering.
- Portal content is untrusted: everything is HTML-escaped, and only `http(s)` links from portal data are made clickable.
- Times are shown as "5 min ago"; hover for the exact time with offset.
- htmx 2.0.4 is vendored in `watchtower/dashboard/static/`, so the page works without internet access.
- Light and dark themes follow the browser setting.

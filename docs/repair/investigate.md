# Investigate (Notice → Repair, audits)

`watchtower/agent/investigate.py` (T-016) answers "what is wrong with this portal, and what template reads it now?".
The read pipeline calls it when a read fails validation (`trigger="failure"`) and on proactive audits
(`trigger="audit"`); it returns a `Diagnosis` ([ADR 0009](../decisions/0009-pipeline-and-agent-outcomes.md)). The
pipeline, not the agent, validates and promotes templates and changes the portal state.

## Flow

Deterministic first ([ADR 0011](../decisions/0011-investigate-deterministic-first.md)):

| Stage | Model? | Decides |
|---|---|---|
| 1. Pre-check: GET the portal address and the template's list address | no | `Gone` (404/410 everywhere), `Blocked` (login/captcha page), `Transient` (no answer, 5xx, 429) |
| 2. Audit evidence (audits only) | no | `Healthy` when nothing is suspicious; otherwise the findings go to stage 3 |
| 3. Agent with the evidence, the structure diff and the tools below | yes | `Changed`, `Healthy` (audit), `Transient`, `Blocked`, `Gone`, `Unrepairable` |

### Audit checks
- **Missed tenders, HTML list:** links on the first list page with the same address pattern as the tender pages the
  read delivered, but not delivered (e.g. a second "new" table).
- **Missed tenders, JSON list:** the site's own page and scripts call the list endpoint with other filter values than
  the template (e.g. `etat=tous` vs `etat=en_cours`).
- **Below the portal's own count** (T-041): the portal's count of its list, read from the template's first list
  page, compared with tenders delivered + dropped by scope. JSON: a total key (`total`, `totalElements`, …). HTML: a
  short text outside the list items, such as `20 Treffer`, `Ergebnisse: 33` or `1–10 von 2.474`. "Per page" choices and
  form options don't count (`engine.completeness.declared_counts`). Below 95 % of it ([ADR 0018](../decisions/0018-completeness-checks.md)
  tolerance) is a finding: pagination that stops early, a capped result list, a filter. The validators' sharp-drop
  check only fires below 60 %, so a list cut from 20 to 15 needs this. Several different counts on the page, or none,
  give no finding. A v2 read that declares `list.total` was already compared by `validate.check`; the audit only notes
  it and makes no request. The first list page is fetched once per audit and shared with the missed-links check.
- **Values from the wrong place:** on 2 open tenders' pages, `buyerName` and `referenceNumber` are compared with the
  value under the portal's own label for that field (`Vergabestelle`, `acheteur`, `órgano de contratación`,
  `zamawiający`, …). A mismatch names the label where the read value actually appears.

## Format v2 templates (T-037)
Investigate reads, audits and repairs [format v2](../engine/template-v2.md) templates as well as v1. A repair keeps
the template's format; `Changed` carries either.

| Stage | On a v2 template |
|---|---|
| Pre-check | Probes the v2 list address: the `api` list request (GET without placeholders), or the page list's `entry` |
| Structure diff | The live side is the snapshot a one-tender v2 read takes of its first list page, the same kind the pipeline stores with every good read. When the list can't be read, the diff says why (e.g. `HTTP 404`) |
| Audit: missed tenders | `api` lists get the site's-own-calls check (`{{today}}` and session placeholders are skipped); plain-HTTP page lists the unread-links check. Browser flows aren't replayed for an audit |
| Audit: own count, labels | As for v1; a v2 read with `list.total` was already compared by `validate.check` |
| Agent | The [explorer tools](../agent/runtime.md#explorer-tools) (`open`, `snapshot`, `act`, `network`, `replay`) and the v2 reference are in the investigate prompt. A failed flow step or view names its action and target: the agent looks up what the control is called now and changes only that. A moved endpoint: `network` shows the call the page makes now, `replay` confirms it |

`show_read` shows the current template as stored (aliases such as `from` and `in`); `diff_structure` and `count_list`
take v1 or v2 templates.

### Live repairs (2026-11-21, strong tier `gpt-6.2-sol`)

| Variant | Diagnosis | Model calls | Tokens in (cached) / out | Time | Repaired read |
|---|---|---|---|---|---|
| `app=api-moved`, v2 API template | `Changed`: list, detail and documents moved to `/app/api/v3` | 8 | 89 999 (42 779) / 2 333 | 51 s | 21/21 open calls, every field and document right |
| `app=relabelled`, v2 browser flow | `Changed`: the link, the *load more* button and the documents tab renamed | 9 | 106 490 (53 229) / 2 885 | 112 s | 21/21, every field and document right |

The first `relabelled` run looked right but only renamed the link: the browser pool's static cache (net, 1 h, no
revalidation) still served the old app script, whose button and tab kept their old names. The mock's scripts now have
a URL per variant, as real apps version their bundles. A real portal that changes a script **at the same URL** would
show the same effect for up to an hour; the next read after that fails and is repaired again.

## Agent tools (investigate only)

| Tool | Returns |
|---|---|
| `show_read()` | The current template, what it read (count, errors, ids, two samples), the validation problems, the usual count |
| `diff_structure(template?)` | Unified diff of the live first list page's structure against the last good read's snapshot |
| `count_list(template)` | List-only read of all pages (≤ 30): count vs the usual count (`OK` / `TOO FEW`), ids lost or gained |
| `report_healthy(notes)` | Ends an audit: the current template is right. Refused for failed reads |
| `report_gone(reason)` · `report_unrepairable(reason)` | End the run with that diagnosis |

Plus the core tools ([agent runtime](../agent/runtime.md)): `fetch`, `render`, `try_template`, `submit_template`
(→ `Changed`, notes become the operators' explanation), `report_blocked`, `report_transient`, `report_unreadable`
(→ `Unrepairable`).

## Fallbacks
- Budget exhausted (failure: 25 turns / 300k tokens / 15 min; audit: 15 / 150k / 10 min): `Unrepairable` for a
  failure; `Transient` for an audit (the read passed validation, so it keeps being served).
- No `OPENAI_API_KEY`: the same, without a model call; stages 1–2 still work.

## Mock-portal coverage
Tested offline in `tests/agent/test_investigate.py` with a scripted model:

| Variant | Diagnosis |
|---|---|
| `gone`, `login-wall` | `Gone` / `Blocked`, no model call |
| `ssr=markup`, `ssr=pagination`, `js=renamed-keys` | `Changed`; the repaired template passes validation with history |
| `ssr=silent-missed`, `ssr=silent-swap`, `js=silent-missed` | audit findings → `Changed`; the repaired read has no findings |
| unchanged `ssr`, `js` | audit `Healthy`, no model call |
| `app=api-moved`, v2 API template (`tests/agent/test_repair_v2.py`) | `Changed`: only the endpoint path changes; also through the pipeline (healing keeps the last tenders, the v2 repair is promoted as version 2) |
| `app=relabelled`, v2 browser flow | `Changed` after `open` shows the new control names (needs Chromium) |
| unchanged `app`, v2 API template | audit `Healthy`, no model call |
| `ssr=silent-cut` (`tests/agent/test_audit_declared_total.py`) | audit finding: "says the list holds 20 ('20 Treffer'), but the read delivered only 15"; the unchanged `ssr`/`js` reads match their own count |

The scripted model supplies the repairs, so these tests prove the plumbing, evidence and validation, not the model's
skill. Check a real model with `OPENAI_API_KEY` set (see [agent runtime → Checking it live](../agent/runtime.md)).

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
- **Values from the wrong place:** on 2 open tenders' pages, `buyerName` and `referenceNumber` are compared with the
  value under the portal's own label for that field (`Vergabestelle`, `acheteur`, `órgano de contratación`,
  `zamawiający`, …). A mismatch names the label where the read value actually appears.

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

The scripted model supplies the repairs, so these tests prove the plumbing, evidence and validation, not the model's
skill. Check a real model with `OPENAI_API_KEY` set (see [agent runtime → Checking it live](../agent/runtime.md)).

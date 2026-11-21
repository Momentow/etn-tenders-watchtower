# Truthful blocks

A portal reported `failed` because of a login or captcha must really be blocked. A portal that could deliver but is
reported blocked is a Notice error. The first live smoke run had two:
[dtvp.de and marches-publics.gouv.fr](../portals/registry.md#smoke-runs). In both, one document link redirected to a
login, but documents download anonymously, after a session cookie or through an anonymous-download option. The rule
comes from [ADR 0015 → Truthful blocks](../decisions/0015-agent-v2-explorer-and-tiers.md#truthful-blocks-t-036) and
is implemented in `watchtower/agent/blocks.py` (T-039).

## Evidence is recorded as the agent works

`ctx.blocks` (`BlockEvidence`) collects, per **tender** (its page URL or id):

| Source | Records |
|---|---|
| `pages.get_page` (`fetch`, `detect_block`, analysis tools) | a page that *is* a login/captcha wall (a strong signal), keyed by the URL asked for, since every blocked page may redirect to the same login page |
| `pages.probe_document` (`probe_document`, `detect_block`, the dry run's document check) | a document that leads to a login/captcha page or answers 401/403 → blocked; a document that is a file → that tender **downloads** |

A tender with at least one downloaded document is never counted as blocked. The dry run probes the first document of
each sampled tender before second documents, so its check covers several tenders.

## `report_blocked` is accepted only on evidence

`blocks.review()` runs inside `report_blocked`:

1. **Whole site:** the entry page is itself a login or captcha wall → accepted.
2. **Firewall:** HTTP 403 on every path of the portal's host (at least 3 paths; the tool fetches `/`, `/robots.txt`
   and `/favicon.ico` if needed) → accepted.
3. **Session:** for up to 2 blocked documents, the tool reloads the page the link came from (or the portal page) and
   tries the document again with the cookies that set. A file now → **disproved**: the engine keeps cookies, so read
   documents from the pages that link to them.
4. **Anonymous routes:** links on the pages seen that offer documents without an account ("download anonymously",
   "Téléchargement anonyme", "sans identification", "anoniem", "ohne Registrierung", "sin registro"…) are followed.
   A file or a normal page → **disproved**. A button or form without a link is reported as **missing** once (try it).
5. **Two tenders:** fewer than 2 tenders blocked → **missing**.

Anything disproved or missing comes back to the model as `Not accepted: …` with each point, and the run goes on.

## How an attach ends

| The run | Outcome |
|---|---|
| `report_blocked` accepted | `Blocked` → `failed` with the model's reason |
| `report_blocked` refused, later a GOOD dry run (or one before) | `TemplateFound` with that template |
| `report_blocked` refused, then the budget ran out | `Unreadable`: "a login or captcha block was suspected (…) but not confirmed: …" |
| `report_transient` while the firewall refuses every path | `Blocked`: "Access refused by the portal's firewall (Microsoft-Azure-Application-Gateway/v2): HTTP 403 on every path tried (N paths)." |

## Tests

`tests/agent/test_truthful_blocks.py` has small local portals for each smoke-run case: the dtvp.de session cookie,
the PLACE anonymous download and the s2c.mercell.com gateway 403. It also covers the true blocks, which stay blocks:
the `login` mock portal with two tenders, and the `login-wall` variant on the entry page.

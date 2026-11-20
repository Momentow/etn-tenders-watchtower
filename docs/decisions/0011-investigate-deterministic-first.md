# 0011 — Investigate: deterministic checks before the model

- **Status:** accepted
- **Date:** 2026-11-20
- **Task:** T-016

## Context
`investigate` (ADR 0002, 0009) runs on every failed read and, in `proactive` mode, once per audit interval for every
serving portal. The "Approach" criterion judges model cost per portal, so a model call on every audit of a healthy
portal is the biggest cost. Many diagnoses don't need judgement at all: a portal that answers 410, redirects to a
login, or doesn't answer. The audit (T-023) and the dashboard need to know when a model was and wasn't asked.

## Decision
`watchtower.agent.investigate.investigate()` runs three stages and stops at the first that decides:

1. **Pre-check** (both triggers, `agent.evidence.precheck`): GET the portal address and the template's list address
   (if it is a GET).
   - every probe 404/410 → `Gone`;
   - a strong login/captcha page (`net.detect_block`) and no probed list page that loads normally → `Blocked`;
   - every probe without an answer, 5xx or 429 → `Transient`.
2. **Audit evidence** (`trigger="audit"`, `agent.evidence.audit`):
   - HTML lists: links on the first list page that share the read tender pages' address pattern but weren't delivered;
   - JSON lists: the site's own pages/scripts calling the list endpoint with other filter values than the template
     (the pagination parameter is ignored);
   - up to 2 tender pages: `buyerName` / `referenceNumber` compared with the value under the portal's own label for
     that field (multilingual label list in `evidence.FIELD_LABELS`).
   **No finding → `Healthy` with no model call.** Findings go to the model as evidence.
3. **Agent** (`run_scenario("investigate", …)`), with the failure problems or audit findings, the address checks and
   a structure diff of the list page against the last good snapshot. Investigate-only tools: `show_read`,
   `diff_structure`, `count_list`, `report_healthy` (refused for failures), `report_gone`, `report_unrepairable`.
   A repair is submitted with the core `submit_template` → `Changed`.

Fallbacks: budget exhausted → `Unrepairable` (failure) / `Transient` (audit, so the validated read stays served,
ADR 0009). No `OPENAI_API_KEY` → the same, without a model call. Budgets: failure 25 turns / 300k tokens / 15 min,
audit 15 turns / 150k tokens / 10 min.

`make_investigate(client, browser)` builds the `Investigate` callable that `app.py` passes to `pipeline.configure`.

## Consequences
- A healthy portal's audit costs a few polite requests (≈ 2–5) and no tokens; model cost is spent only on findings and
  failures. The audit only catches what the checks look for; a new kind of silent breakage needs a new check here.
- T-023 schedules audits and shows their details; it needs no change for the model/no-model split.
- The checks reuse the engine's parser and `net`, so they obey the per-host rate limit.
- T-022 tools are offered to the investigate agent automatically when registered for `INVESTIGATE`.

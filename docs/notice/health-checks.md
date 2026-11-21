# Health checks: reactive and proactive

How Watchtower notices that a portal stopped delivering correctly (T-023). The mode is set with
`WATCHTOWER_HEALTHCHECK_MODE`; the design is in [ADR 0002 → Health-check modes](../decisions/0002-architecture.md#health-check-modes).

| Mode | Notices | Cost |
|---|---|---|
| `reactive` | Reads that crash or fail validation ([checks](../validate/checks.md)) | A model call only when a read fails and a re-read doesn't fix it |
| `proactive` (default) | All of the above, **plus** silent problems: tenders the template misses, values read from the wrong place | Also a few polite requests per portal per interval; a model call only when the audit finds something |

`WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` (default `24`) sets how often each `serving` portal is audited.

## Reactive (both modes)

```
read fails validation or crashes → healing (last correct tenders still served) → repair run:
  plain re-read passes                  → serving ("the failure was temporary"; no model call)
  investigate(trigger="failure"):
    Changed + new template validates    → serving, new template version promoted
    Transient + re-read passes          → serving
    Blocked / Gone / Unrepairable       → failed, with the exact reason
```

Gone (404/410), login walls and outages are recognised by [investigate's pre-check](../repair/investigate.md)
without a model call.

## Proactive audits

- The scheduler requests an audit for each `serving`, idle portal once per interval. A portal's first audit is
  staggered by a stable fraction of the interval after it was added, and at most one audit starts per tick (15 s), so
  audits are spread out ([scheduler](../core/scheduler.md)).
- An audit is an `investigate` job recorded as a `read` run (`details.trigger = "audit"`, `details.audit = true`):
  1. the portal is read with the current template; if that fails validation it is an ordinary failed read;
  2. otherwise `investigate(trigger="audit")` compares the read with the live portal
     ([audit checks](../repair/investigate.md#audit-checks)). Nothing suspicious → **Healthy, with no model call**;
     the tenders are delivered and the portal stays `serving`.
  3. A finding goes to the agent. `Changed` → the proposed template is stored unpromoted and the portal goes to
     `healing` (reason "The scheduled audit found a problem: …"); the repair run (`trigger="audit_finding"`) promotes
     it only if it validates. Any other finding → `healing`, then `failed` with the audit's reason
     ([ADR 0013](../decisions/0013-audit-finding-repair.md)): the portal never returns to `serving` while the silent
     problem is still there.
- A crashed audit changes nothing; the next one is due again later.

## On the dashboard
- The overview says which mode is active and shows, per portal, the last audit's result (healthy / found a problem),
  when it ran and when the next one is due.
- In a portal's run timeline, audits are labelled **Health audit** and repairs they started **Repair after audit**;
  the "What happened" column shows the diagnosis and its reason, e.g. "Healthy: all 10 tender links on the first list
  page were delivered by the read".

## Tests
`tests/agent/test_health_checks.py` drives the real scheduler, pipeline and investigate scenario (scripted model) on
the mock portals with a controlled clock:

| Test | Shows |
|---|---|
| reactive + `ssr=silent-swap` | the wrong buyer names are served and nothing notices (no audit, no model call) |
| proactive + `ssr=silent-swap` | the audit finds the swapped buyer → healing → repaired template v2 → correct buyer names; the next audit is healthy with no model call |
| proactive, unchanged portal | audit healthy, no model call |
| proactive + `ssr=silent-cut` | the list is capped at 15 of 20 hits; validation passes, but the audit compares the read with the page's "20 Treffer" → finding → `failed` with the agent's reason, not a partial `serving` (T-041) |
| proactive, finding the agent can't repair | `failed` with the agent's reason, not a false "temporary" repair |
| both modes + `ssr=markup` | failed read → healing → repaired → serving |
| `gone`, `login-wall` | `failed` with an exact reason, no model call |

# Scheduler and state machine

`watchtower.core.scheduler` runs persisted jobs on asyncio workers and keeps portals read and audited on schedule.
`watchtower.core.state_machine` is the only code that changes a portal's `state` and `reason`.
The rules both follow are in [ADR 0006](../decisions/0006-job-handlers-and-state-events.md) and
[ADR 0005](../decisions/0005-run-requests.md).

## Writing a handler (T-012, T-017)

```python
from watchtower.core.handlers import register
from watchtower.core.scheduler import JobContext

@register("read")
async def handle_read(ctx: JobContext) -> None:
    if await ctx.portal_deleted():
        return
    ...                                   # engine run, validate.check, upsert tenders
    await ctx.store.finish_run(ctx.run_id, "ok", tender_count=n, snapshot=snapshot)
    await ctx.states.apply(ctx.job.portal_id, "read_succeeded", tender_count=n)
    # on a failed validation instead:
    # await ctx.store.finish_run(ctx.run_id, "failed", details={"problems": problems})
    # await ctx.states.start_healing(ctx.job.portal_id, "; ".join(problems))
```

Raise to fail the attempt: the scheduler retries, then gives up safely (closes the run, applies the failure event).

## What runs when

| What | When |
|---|---|
| Re-queue jobs left `running` | At start-up |
| Workers (4) | Continuously; only job kinds with a registered handler are taken |
| Tick | Every 15 s: cancel jobs of deleted portals, request due reads, start up to one due audit |
| Periodic read | `serving` portal, last finished read older than 1 h |
| Audit (`proactive`) | Every `WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` per `serving` portal, staggered ([health checks](../notice/health-checks.md)). `Scheduler.audit_schedule()` gives each portal's last and next audit (dashboard) |

Environment: `WATCHTOWER_HEALTHCHECK_MODE` (`reactive` / `proactive`, default `proactive`) and
`WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS` (default 24). A bad value stops start-up with a clear error.

Tests: `uv run pytest tests/core/test_scheduler.py tests/core/test_state_machine.py`.

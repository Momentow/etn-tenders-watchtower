# End-to-end tests and smoke runs

Two ways to check the whole system the way a judge would: through the public API only, given nothing but a portal's URL.

## End-to-end tests (no model key, runs in CI)

`tests/agent/test_e2e_api.py` starts the real app (`create_app`) and talks to it only through HTTP. Portal traffic goes to
the [mock portals](mock-portals.md), and a scripted model stands in for the LLM, so the run is deterministic and free.
Jobs are drained in-process instead of waiting for the workers' timers; everything else is the production code.

| Test | What it proves |
|---|---|
| attach | `POST /portals` → `serving`; every expected tender with the right values; documents download and `/files` returns the bytes their `sha256` names |
| changed portal | `markup` variant → the read fails → `healing`, **the last correct tenders keep being served** → repair → `serving` with the new values |
| captcha widget | A captcha is shown, but the list and documents work: the portal ends `serving`, even when the model calls it blocked. Needs Chromium (skipped without it) |
| blocked | Robot check (captcha) and login → `failed`, `reason` says why, no tenders |

```bash
uv run pytest tests/agent/test_e2e_api.py
```

## Smoke runs (real model, live)

`testportals/smoke.py` adds a set of portals to a **running** Watchtower, prints every state change as it happens, and
ends with a table: state, tenders, tenders with documents, files, a match against the expected tenders, time to settle,
and the reason. It uses only the public API, so the dashboard of the same service shows the run live.

```bash
uv run python -m mockportals                                  # only for the mock set, on :8081
DATA_DIR=./data uv run --env-file .env python -m watchtower   # dashboard on http://localhost:8080/
uv run python testportals/smoke.py mock                       # or dev, blocked, unverified, unseen
uv run python testportals/smoke.py dev --only tenderned.nl --out smoke-dev.json
```

- **Sets** come from the `use` column of [`testportals/portals.json`](../portals/registry.md); `mock` is the seven mock
  portals. Don't run `unseen` until the final test.
- **Match.** Mock portals are compared exactly with `expected_tenders()` (ids, values, document names) and with the state
  the mock expects. Real portals are matched approximately against the TED fixtures on reference number or title: the
  fixtures cover a 30-day window, so some of their notices have closed since.
- **Exit code** 0 when every portal settled (`serving` or `failed`) without problems; 1 otherwise.
- Running it again is safe: the same URL returns the same portal id without a second attach, so it just reports again.
  Documents keep downloading after a portal reaches `serving`, so a later report can show more files.
- The script calls only our own API. All portal traffic goes through Watchtower, at most 1 request per second per portal.

## Demo scenario (live, real portals)

The run we record for the demo video, and our realistic test of the whole system:

1. Start the service on an empty data directory and open the dashboard at `http://localhost:8080/`: no portals yet.
2. Add 12 real portals a few seconds apart: the 7 `dev` portals, the 3 `blocked` ones (they must end `failed` with a
   reason) and the 2 `unverified` ones. The `unseen` portals stay held back for the final test.

   ```bash
   DATA_DIR=$(mktemp -d) uv run --env-file .env python -m watchtower
   uv run python testportals/smoke.py dev blocked unverified --stagger 5 --timeout 120
   ```

3. Watch the dashboard: each portal goes `integrating` → `serving` or `failed`, with the reason in words and its runs in
   the timeline. The terminal prints the same state changes and ends with the results table.

Results are recorded in the [registry](../portals/registry.md#smoke-runs).

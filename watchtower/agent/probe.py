"""Developer check: run the agent loop live on one portal and print the outcome, tool trace and token usage.

    uv run --env-file .env python -m watchtower.agent.probe http://localhost:8081/ssr/      # start mockportals first
    uv run --env-file .env python -m watchtower.agent.probe --mock ssr                      # serves the mocks itself

Runs the real `generate_template` scenario (T-015) and prints its raw run plus the outcome the pipeline would get.
Costs real tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys

from watchtower.agent.attach import run_attach
from watchtower.agent.outcomes import TemplateFound
from watchtower.agent.runtime import Budget
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient


async def probe(url: str, *, browser: bool, budget: Budget) -> int:
    limiter = HostRateLimiter()
    async with PoliteClient(limiter=limiter) as client:
        pool = BrowserPool(limiter=limiter) if browser else None
        try:
            result = await run_attach(url, client, pool, budget=budget)
        finally:
            if pool is not None:
                await pool.aclose()
    run, outcome = result.run, result.outcome
    if run is not None:
        print(f"\nstop: {run.stop_reason}   raw outcome: {type(run.outcome).__name__ if run.outcome else None}")
        print(
            f"model calls: {run.requests}   tokens: {run.input_tokens} in ({run.cached_tokens} cached) / "
            f"{run.output_tokens} out   time: {run.seconds:.0f}s"
        )
        if run.outcome is None and run.final_text:
            print("final text:", run.final_text)
    print(f"outcome: {type(outcome).__name__}")
    if isinstance(outcome, TemplateFound):
        print("notes:", outcome.notes)
        print(json.dumps(outcome.template.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False))
        return 0
    print("reason:", outcome.reason)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--mock", help="serve the mock portals on :8081 and probe this kind (ssr, js, form, …)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=400_000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("watchtower.agent").setLevel(logging.INFO)
    budget = Budget(max_turns=args.max_turns, max_tokens=args.max_tokens)

    serve = contextlib.nullcontext()
    url = args.url
    if args.mock:
        from mockportals.app import ENTRY
        from mockportals.testing import serve as serve_mocks

        serve = serve_mocks(port=8081)
        url = f"http://localhost:8081{ENTRY[args.mock]}"
    if not url:
        ap.error("give a URL or --mock KIND")
    with serve:
        return asyncio.run(probe(url, browser=not args.no_browser, budget=budget))


if __name__ == "__main__":
    sys.exit(main())

"""Run the mock portals inside a test.

    with serve() as base:                      # real HTTP server on 127.0.0.1:8081 (the example templates' address)
        base.app.state.variants["ssr"] = "markup"   # mutate a running portal

For tests that don't need a socket, use `httpx.ASGITransport(app=create_app())` with base_url `http://localhost:8081`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from mockportals.app import create_app


@dataclass
class Served:
    url: str
    app: FastAPI


@contextmanager
def serve(port: int = 8081, variants: dict[str, str] | None = None) -> Iterator[Served]:
    app = create_app(variants)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"mock portals did not start on port {port}")
        time.sleep(0.02)
    try:
        yield Served(f"http://127.0.0.1:{port}", app)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

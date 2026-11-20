"""FastAPI app factory: one process serves the API, the dashboard and /docs."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from watchtower.agent.attach import make_generate_template
from watchtower.agent.investigate import make_investigate
from watchtower.api.routes import router as api_router
from watchtower.core import pipeline  # registers the attach/read/investigate job handlers
from watchtower.core.handlers import HANDLERS
from watchtower.core.scheduler import HealthcheckSettings, Scheduler
from watchtower.core.store import Store
from watchtower.dashboard.routes import router as dashboard_router
from watchtower.docs_site import mount_docs
from watchtower.documents import handler as documents_handler  # registers the download job handler
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient


def database_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "watchtower.db"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Opening the store applies pending migrations (T-004). Other modules use `app.state.store`.
    healthcheck = HealthcheckSettings.from_env()  # fail at start-up on a bad env var, not hours later
    store = await Store.open(database_path())
    app.state.store = store
    # One limiter for all portal traffic (docs/net/network.md); modules reuse app.state.http.
    app.state.limiter = HostRateLimiter()
    app.state.http = PoliteClient(limiter=app.state.limiter)
    documents_handler.use_client(app.state.http)
    # Chromium starts lazily, on the first template that needs rendering (T-017).
    app.state.browser = BrowserPool(limiter=app.state.limiter)
    # Agent scenarios (ADR 0009): generate_template (T-015) and investigate (T-016).
    pipeline.configure(
        client=app.state.http,
        browser=app.state.browser,
        scenarios=pipeline.Scenarios(
            generate_template=make_generate_template(app.state.http, app.state.browser),
            investigate=make_investigate(app.state.http, app.state.browser),
        ),
    )
    # Scheduler and workers (T-006). Handlers come from the modules that register them (T-012, T-017).
    scheduler = Scheduler(store, HANDLERS, healthcheck=healthcheck)
    app.state.scheduler = scheduler
    if app.state.run_scheduler:
        await scheduler.start()
    try:
        yield
    finally:
        if app.state.run_scheduler:
            await scheduler.stop()
        await app.state.browser.aclose()
        await app.state.http.aclose()
        await store.close()


def create_app(*, run_scheduler: bool = True) -> FastAPI:
    # /docs is the project docs site (T-019), so FastAPI's own Swagger/ReDoc pages are switched off.
    app = FastAPI(title="Watchtower", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.run_scheduler = run_scheduler
    app.include_router(api_router)
    app.include_router(dashboard_router)
    mount_docs(app)
    return app

"""FastAPI app factory: one process serves the API, the dashboard and /docs."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from watchtower.api.routes import router as api_router
from watchtower.core.handlers import HANDLERS
from watchtower.core.scheduler import HealthcheckSettings, Scheduler
from watchtower.core.store import Store
from watchtower.dashboard.routes import router as dashboard_router


def database_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "watchtower.db"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Opening the store applies pending migrations (T-004). Other modules use `app.state.store`.
    healthcheck = HealthcheckSettings.from_env()  # fail at start-up on a bad env var, not hours later
    store = await Store.open(database_path())
    app.state.store = store
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
        await store.close()


def create_app(*, run_scheduler: bool = True) -> FastAPI:
    # /docs is the project docs site (T-019), so FastAPI's own Swagger/ReDoc pages are switched off.
    app = FastAPI(title="Watchtower", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.run_scheduler = run_scheduler
    app.include_router(api_router)
    app.include_router(dashboard_router)
    return app

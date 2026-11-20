"""FastAPI app factory: one process serves the API, the dashboard and /docs."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from watchtower.core.store import Store
from watchtower.dashboard.routes import router as dashboard_router


def database_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "watchtower.db"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Opening the store applies pending migrations (T-004). Other modules use `app.state.store`.
    store = await Store.open(database_path())
    app.state.store = store
    try:
        yield
    finally:
        await store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Watchtower", lifespan=lifespan)
    app.include_router(dashboard_router)
    return app

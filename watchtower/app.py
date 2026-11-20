"""FastAPI app factory: one process serves the API, the dashboard and /docs."""

from fastapi import FastAPI

from watchtower.dashboard.routes import router as dashboard_router


def create_app() -> FastAPI:
    app = FastAPI(title="Watchtower")
    app.include_router(dashboard_router)
    return app

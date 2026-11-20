"""The docs site at `/docs` (T-019): static files built from `docs/` by Zensical (`mkdocs.yml`).

The Docker image builds the site into `/app/site`. Locally, build it with `uv run --group docs zensical build`; until
then `/docs` shows a short page saying how.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

SITE_DIR = Path(__file__).resolve().parents[1] / "site"

_NOT_BUILT = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Watchtower docs</title></head>
<body><h1>Docs not built</h1>
<p>Build the docs site with <code>uv run --group docs zensical build</code> and restart. The Docker image builds it.</p>
</body>
</html>
"""


def mount_docs(app: FastAPI, site_dir: Path = SITE_DIR) -> None:
    @app.get("/docs", include_in_schema=False)
    async def docs_root() -> RedirectResponse:  # relative links in the site need the trailing slash
        return RedirectResponse("/docs/", status_code=308)

    if (site_dir / "index.html").is_file():
        app.mount("/docs", StaticFiles(directory=site_dir, html=True), name="docs")
        return

    @app.get("/docs/{path:path}", include_in_schema=False)
    async def docs_not_built(path: str) -> HTMLResponse:
        return HTMLResponse(_NOT_BUILT, status_code=503)

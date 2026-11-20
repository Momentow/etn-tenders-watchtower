"""Docs site at /docs (T-019): served from the built site, with a hint page until it's built."""

import httpx
from fastapi import FastAPI

from watchtower.docs_site import mount_docs


def client_for(site_dir) -> httpx.AsyncClient:
    app = FastAPI(docs_url=None, redoc_url=None)
    mount_docs(app, site_dir)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_built_site_is_served_under_docs(tmp_path):
    (tmp_path / "index.html").write_text("<h1>home</h1>")
    (tmp_path / "core" / "pipeline").mkdir(parents=True)
    (tmp_path / "core" / "pipeline" / "index.html").write_text("<h1>pipeline</h1>")
    (tmp_path / "404.html").write_text("<h1>missing</h1>")
    async with client_for(tmp_path) as c:
        r = await c.get("/docs")
        assert r.status_code == 308 and r.headers["location"] == "/docs/"
        assert (await c.get("/docs/")).text == "<h1>home</h1>"
        assert (await c.get("/docs/core/pipeline/")).text == "<h1>pipeline</h1>"
        r = await c.get("/docs/nope/")
        assert r.status_code == 404 and "missing" in r.text


async def test_unbuilt_site_says_how_to_build_it(tmp_path):
    async with client_for(tmp_path / "absent") as c:
        r = await c.get("/docs/")
        assert r.status_code == 503 and "zensical build" in r.text


def test_every_docs_page_is_in_the_nav():
    """A page missing from `nav` in mkdocs.yml is built but can't be found from the menu."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "mkdocs.yml").read_text(encoding="utf-8"))

    def pages(node) -> set[str]:
        if isinstance(node, str):
            return {node}
        if isinstance(node, list):
            return set().union(*(pages(n) for n in node))
        return set().union(*(pages(v) for v in node.values()))

    in_nav = pages(config["nav"])
    on_disk = {p.relative_to(root / "docs").as_posix() for p in (root / "docs").rglob("*.md")}
    assert on_disk - in_nav == set(), "add these pages to `nav` in mkdocs.yml"
    assert in_nav - on_disk == set(), "these `nav` entries have no page"

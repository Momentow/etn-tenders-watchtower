"""BrowserPool against a tiny local site. Skipped when Playwright's Chromium isn't installed
(install with `uv run playwright install chromium`; the Docker image already has it)."""

import asyncio
import json
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from watchtower.net import BrowserPool, HostRateLimiter


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_installed(), reason="Playwright Chromium not installed")

PAGE = """<!doctype html><html><head><title>JS portal</title></head><body>
<ul id=list></ul><img src="/logo.png">
<script>
fetch('/api/tenders.json').then(r => r.json()).then(data => {
  for (const t of data.items) {
    const li = document.createElement('li'); li.textContent = t.title; document.getElementById('list').appendChild(li);
  }
});
</script></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE)
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "tenders.json").write_text(json.dumps({"items": [{"title": "Road works"}, {"title": "IT"}]}))
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    hits: list[tuple[float, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            hits.append((time.monotonic(), self.path))

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


async def test_render_returns_html_and_captured_json(site):
    base, hits = site
    pool = BrowserPool(limiter=HostRateLimiter(0.3))
    try:
        result = await pool.render(base + "/index.html")
    finally:
        await pool.aclose()

    assert result.status == 200
    assert "<li>Road works</li>" in result.html
    [api] = result.json_responses()
    assert api.url.endswith("/api/tenders.json") and json.loads(api.body)["items"][1]["title"] == "IT"

    paths = [p for _, p in hits]
    assert not any("logo.png" in p for p in paths)  # images are never requested
    times = [t for t, _ in hits]
    assert all(b - a >= 0.25 for a, b in zip(times, times[1:], strict=False))


async def test_concurrent_renders_share_the_limiter(site):
    base, hits = site
    pool = BrowserPool(limiter=HostRateLimiter(0.3), max_pages=2)
    try:
        await asyncio.gather(pool.render(base + "/index.html"), pool.render(base + "/index.html"))
    finally:
        await pool.aclose()
    times = sorted(t for t, _ in hits)
    assert len(times) >= 4
    assert all(b - a >= 0.25 for a, b in zip(times, times[1:], strict=False))

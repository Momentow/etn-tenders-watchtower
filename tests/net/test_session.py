"""Browser sessions (ADR 0014 §1, T-032) against a local site shaped like tenderned.nl: a home page with an "All
tenders" link, an app that boots through many scripts, a list with "Load more" over a JSON API, and a detail page with
tabs. Skipped when Playwright's Chromium isn't installed."""

import json
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from watchtower.net import Action, BrowserPool, HostRateLimiter, SessionBudget, SessionBudgetExceeded, Target

A = TypeAdapter(Action)
SCRIPTS = 12  # the app's boot chunks
PAGES = 3  # list pages behind "Load more", 5 tenders each


def act(**kw):
    return A.validate_python(kw)


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


CHROMIUM = _chromium_installed()
browser_only = pytest.mark.skipif(not CHROMIUM, reason="Playwright Chromium not installed")

HOME = """<!doctype html><html><head><title>Portal</title></head><body>
<h1>Public procurement</h1><a href="/about.html">About</a> <a href="/list.html">All tenders</a></body></html>"""

# The list page boots like an app: chunk 0 loads chunk 1 … the last one fetches the first list page.
LIST = """<!doctype html><html><head><title>Tenders</title></head><body>
<h1>Tenders</h1><p id=total></p><ul id=list></ul><button id=more hidden>Load more</button>
<script src="/js/chunk-0.js"></script></body></html>"""

CHUNK = """(() => {{ const s = document.createElement('script'); s.src = '/js/chunk-{next}.js';
document.head.appendChild(s); }})();"""

APP = """window.page = 0;
function show(data) {
  document.getElementById('total').textContent = data.total + ' results';
  for (const t of data.items) {
    const li = document.createElement('li'); li.className = 'tender';
    li.innerHTML = '<a href="/detail.html?id=' + t.id + '">' + t.title + '</a>';
    document.getElementById('list').appendChild(li);
  }
  document.getElementById('more').hidden = data.last;
}
function load() { fetch('/api/items-' + window.page + '.json').then(r => r.json()).then(show); window.page++; }
document.getElementById('more').addEventListener('click', load);
load();"""

DETAIL = """<!doctype html><html><head><title>Tender</title></head><body><h1>Road works</h1>
<div role=tablist><button role=tab id=t1>Overview</button><button role=tab id=t2>Documents</button></div>
<div id=panel>Deadline: 01.12.2026</div>
<script>
document.getElementById('t2').addEventListener('click', () =>
  fetch('/api/docs.json').then(r => r.json()).then(d => {
    document.getElementById('panel').innerHTML = d.files.map(f => '<a href="/files/' + f + '">' + f + '</a>').join(' ');
  }));
</script></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(HOME)
    (tmp_path / "about.html").write_text("<html><body>About</body></html>")
    (tmp_path / "list.html").write_text(LIST)
    (tmp_path / "detail.html").write_text(DETAIL)
    (tmp_path / "js").mkdir()
    for i in range(SCRIPTS - 1):
        (tmp_path / "js" / f"chunk-{i}.js").write_text(CHUNK.format(next=i + 1))
    (tmp_path / "js" / f"chunk-{SCRIPTS - 1}.js").write_text(APP)
    (tmp_path / "api").mkdir()
    for p in range(PAGES):
        items = [{"id": f"T-{p}{i}", "title": f"Tender {p}{i} with a long enough title"} for i in range(5)]
        body = {"total": PAGES * 5, "items": items, "last": p == PAGES - 1}
        (tmp_path / "api" / f"items-{p}.json").write_text(json.dumps(body))
    (tmp_path / "api" / "docs.json").write_text(json.dumps({"files": ["Leistungsverzeichnis.pdf", "Plan.pdf"]}))
    hits: list[tuple[float, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self):
            if self.path.endswith(".js"):
                self.send_header("Cache-Control", "max-age=3600")
            super().end_headers()

        def log_message(self, *a):
            hits.append((time.monotonic(), self.path))

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@pytest.fixture
async def pool():
    p = BrowserPool(limiter=HostRateLimiter(0.25))
    yield p
    await p.aclose()


# ---------------------------------------------------------------- actions as data (no browser)


def test_target_needs_exactly_one_kind():
    assert Target(role="button", name="Load more").describe() == "button 'Load more'"
    for bad in ({}, {"css": "a", "text": "x"}, {"name": "x"}, {"role": "banana"}):
        with pytest.raises(ValidationError):
            Target(**bad)


def test_actions_are_a_closed_set_without_javascript():
    assert act(do="load_more", target={"css": "button.more"}).max_clicks == 20
    with pytest.raises(ValidationError):
        act(do="evaluate", script="alert(1)")
    with pytest.raises(ValidationError):
        act(do="wait_for", text="x", response="api")  # exactly one condition
    with pytest.raises(ValidationError):
        act(do="goto", url="javascript:alert(1)")


# ---------------------------------------------------------------- the polite clock


@browser_only
async def test_a_slow_polite_boot_is_not_a_timeout(site, pool):
    """12 chunked scripts + the list call at 0.25 s spacing: ~3.5 s of waiting, under 2 s of *active* budget."""
    base, hits = site
    async with pool.session(budget=SessionBudget(active_seconds=2.0)) as s:
        r = await s.act(act(do="goto", url=base + "/list.html"))
        assert r.error is None
        assert r.seconds_waiting > 2.0 and r.seconds_active < 2.0
        html = await s.html()
        assert "15 results" in html and html.count('class="tender"') == 5  # the first list page, nothing more
        assert [e.url.rsplit("/", 1)[1] for e in r.network] == ["items-0.json"]

    times = sorted(t for t, _ in hits)
    assert all(b - a >= 0.22 for a, b in zip(times, times[1:], strict=False))  # still 1 request per interval


@browser_only
async def test_render_survives_a_boot_longer_than_its_timeout(site):
    pool = BrowserPool(limiter=HostRateLimiter(0.25), timeout_ms=2000)
    try:
        r = await pool.render(site[0] + "/list.html")
    finally:
        await pool.aclose()
    assert "Tender 00" in r.html and r.json_responses()


@browser_only
async def test_budget_runs_out_while_busy(site, pool):
    async with pool.session(budget=SessionBudget(active_seconds=3.0)) as s:
        await s.act(act(do="goto", url=site[0] + "/index.html"))  # ~1.6 s: settling waits 1.5 s of quiet
        with pytest.raises(SessionBudgetExceeded):
            await s.act(act(do="wait_for", response="never-called"))


# ---------------------------------------------------------------- what a person does


@browser_only
async def test_home_click_load_more_detail_tab(site, pool):
    base, _ = site
    async with pool.session() as s:
        await s.act(act(do="goto", url=base + "/"))
        r = await s.act(act(do="click", target={"role": "link", "name": "All tenders"}))
        assert r.error is None and s.url.endswith("/list.html")

        r = await s.act(act(do="load_more", target={"role": "button", "name": "Load more"}, max_clicks=10))
        assert r.note == "2 clicks; the control is gone"
        assert [e.url.rsplit("/", 1)[1] for e in r.network] == ["items-1.json", "items-2.json"]
        assert all(e.action_index == 2 for e in r.network)
        html = await s.html()
        assert html.count('class="tender"') == 15

        snap = await s.snapshot()
        assert "Totals shown: 15 results" in snap
        assert "Tender 22 with a long enough title → /detail.html?id=T-22" in snap
        assert "Links (15; item-like first):\n  Tender 00 with a long enough title → /detail.html?id=T-00" in snap

        await s.act(act(do="click", target={"text": "Tender 00 with a long enough title"}))
        assert "Tabs: Overview | Documents" in await s.snapshot()
        r = await s.act(act(do="tab", target={"role": "tab", "name": "Documents"}))
        assert [e.url.rsplit("/", 1)[1] for e in r.network] == ["docs.json"]
        assert json.loads(r.network[0].body)["files"][0] == "Leistungsverzeichnis.pdf"
        assert "Plan.pdf" in await s.html()

        # The replay data an agent needs: method, URL, request headers without cookies.
        assert r.network[0].method == "GET" and "cookie" not in r.network[0].request_headers
        assert [e.url.rsplit("/", 1)[1] for e in s.network] == [
            "items-0.json",
            "items-1.json",
            "items-2.json",
            "docs.json",
        ]
        assert [e.id for e in s.network] == [0, 1, 2, 3]


@browser_only
async def test_a_missing_target_is_an_error_not_a_crash(site, pool):
    async with pool.session() as s:
        await s.act(act(do="goto", url=site[0] + "/"))
        r = await s.act(act(do="click", target={"role": "button", "name": "Nope"}))
        assert r.error == "nothing on the page matches button 'Nope'"
        r = await s.act(act(do="click", target={"role": "link", "name": "About"}))  # still usable
        assert r.error is None and s.url.endswith("/about.html")


@browser_only
async def test_cookies_carry_across_actions(site, pool):
    async with pool.session() as s:
        await s.act(act(do="goto", url=site[0] + "/"))
        await s._context.add_cookies([{"name": "sid", "value": "abc", "url": site[0]}])
        await s.act(act(do="click", target={"role": "link", "name": "All tenders"}))
        assert [c["value"] for c in await s.cookies() if c["name"] == "sid"] == ["abc"]


# ---------------------------------------------------------------- the static cache


@browser_only
async def test_second_boot_serves_scripts_from_the_cache(site, pool):
    base, hits = site
    for _ in range(2):
        async with pool.session() as s:
            await s.act(act(do="goto", url=base + "/list.html"))
            counts = s.counts
    script_hits = [p for _, p in hits if p.endswith(".js")]
    assert len(script_hits) == SCRIPTS  # each chunk fetched once over two boots
    assert counts["cache_hit"] == SCRIPTS
    assert sum(1 for _, p in hits if "items-0.json" in p) == 2  # data is never cached


@browser_only
async def test_a_held_session_and_a_short_one_never_wait_for_each_other(site):
    """An agent run holds its explorer session for the whole run and opens short sessions (renders, dry runs) inside
    it. With one slot of each kind both open at once: held sessions have slots of their own (no deadlock)."""
    import asyncio

    base, _ = site
    pool = BrowserPool(limiter=HostRateLimiter(0), max_pages=1, max_held=1)
    try:
        async with pool.session(held=True) as outer:
            await outer.act(act(do="goto", url=base + "/about.html"))
            rendered = await asyncio.wait_for(pool.render(base + "/about.html"), timeout=60)
            assert "About" in rendered.html
    finally:
        await pool.aclose()

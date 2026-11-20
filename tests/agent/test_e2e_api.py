"""End to end through the public API (T-020): the whole app, the mock portals and a scripted model.

Only the HTTP API is used to add portals and check results, the way a judge would. Jobs are drained in-process
instead of waiting for the workers' timers; everything else (scheduler, pipeline, engine, validation, downloads,
store, API) is the real code.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from scripted import ScriptedModel, call
from test_investigate import _markup, edit, repair_script

from mockportals.app import ENTRY
from mockportals.app import create_app as create_mock
from mockportals.data import expected_tenders
from mockportals.testing import serve
from watchtower.agent.attach import make_generate_template
from watchtower.agent.investigate import make_investigate
from watchtower.app import create_app
from watchtower.core import pipeline
from watchtower.core.times import utcnow
from watchtower.documents import handler as documents_handler
from watchtower.engine.examples import load_example
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient

BASE = "http://localhost:8081"
COMPARED = ("title", "buyerName", "publicationDate", "submissionDeadline", "status", "referenceNumber")


def chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


CHROMIUM = chromium_installed()  # checked once, outside any event loop (the sync API refuses to run in one)


class E2E:
    """The Watchtower app with its portal traffic routed to the mock portals at `base`."""

    def __init__(self, app, api: httpx.AsyncClient, mock, base: str):
        self.app, self.api, self.mock, self.base = app, api, mock, base

    def use_models(self, attach: list, repair: list | None = None) -> None:
        portals, browser = self.app.state.http, self.app.state.browser
        pipeline.configure(
            scenarios=pipeline.Scenarios(
                generate_template=make_generate_template(portals, browser, model=ScriptedModel(attach)),
                investigate=make_investigate(portals, browser, model=ScriptedModel(repair or [])),
            )
        )

    def template(self, name: str) -> str:
        """An example template, as JSON, pointed at this run's mock portals."""
        return load_example(name).model_dump_json(exclude_defaults=True).replace(BASE, self.base)

    async def drain(self) -> None:
        store, scheduler = self.app.state.store, self.app.state.scheduler
        while job := await store.claim_next_job(now=utcnow() + timedelta(days=1)):
            await scheduler.run_job(job)

    async def add(self, kind: str) -> str:
        r = await self.api.post("/portals", json={"url": self.base + ENTRY[kind]})
        assert r.status_code == 201
        await self.drain()
        return r.json()["id"]

    async def get(self, path: str):
        r = await self.api.get(path)
        assert r.status_code == 200, (path, r.status_code)
        return r.json()


@asynccontextmanager
async def running(tmp_path, monkeypatch, mock, base: str, transport=None) -> AsyncIterator[E2E]:
    """The real app, with a portal client and browser that skip the 1 s spacing (the mock portals are local)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app(run_scheduler=False)
    saved = pipeline._config
    async with app.router.lifespan_context(app):
        await app.state.http.aclose()
        await app.state.browser.aclose()
        limiter = HostRateLimiter(0)
        app.state.http = PoliteClient(limiter=limiter, transport=transport, max_attempts=1)
        app.state.browser = BrowserPool(limiter=limiter)
        pipeline.configure(client=app.state.http, browser=app.state.browser)
        documents_handler.use_client(app.state.http)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
                yield E2E(app, api, mock, base)
        finally:
            await app.state.http.aclose()
            await app.state.browser.aclose()
    pipeline._config = saved


@pytest.fixture
async def e2e(tmp_path, monkeypatch):
    """Mock portals in-process, no socket: for portals that don't need the browser."""
    mock = create_mock()
    async with running(tmp_path, monkeypatch, mock, BASE, httpx.ASGITransport(app=mock)) as e:
        yield e


@pytest.fixture
async def e2e_browser(tmp_path, monkeypatch):
    """Mock portals on a real port, so Chromium can load them."""
    if not CHROMIUM:
        pytest.skip("Playwright Chromium not installed")
    with serve(port=18083) as served:
        async with running(tmp_path, monkeypatch, served.app, served.url) as e:
            yield e


def attach_script(t: str) -> list:
    return [[call("try_template", template=t, sample=2)], [call("submit_template", template=t, notes="table + detail")]]


async def assert_delivers(e2e: E2E, pid: str, kind: str, variant: str = "base") -> list[dict]:
    """Every expected tender is served with the expected values, and its documents download with matching bytes."""
    tenders = await e2e.get(f"/portals/{pid}/tenders")
    got = {t["id"]: t for t in tenders}
    want = expected_tenders(kind, variant)
    assert sorted(got) == sorted(t["id"] for t in want)
    for w in want:
        t = got[w["id"]]
        assert {k: t.get(k) for k in COMPARED if k in w} == {k: w[k] for k in COMPARED if k in w}, w["id"]
        assert sorted(d["fileName"] for d in t["documents"]) == w["documents"], w["id"]
    for doc in next(t for t in tenders if t["documents"])["documents"]:
        r = await e2e.api.get(doc["path"])
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == doc["sha256"]
        assert len(r.content) == doc["sizeBytes"]
    return tenders


async def test_attach_delivers_every_tender_and_its_documents(e2e):
    e2e.use_models(attach_script(e2e.template("ssr_list_detail")))
    pid = await e2e.add("ssr")
    portal = await e2e.get(f"/portals/{pid}")
    assert portal["state"] == "serving", portal["reason"]
    await assert_delivers(e2e, pid, "ssr")
    [run] = await e2e.get(f"/portals/{pid}/runs")
    assert run["result"] == "ok"


async def test_a_changed_portal_heals_while_serving_its_last_tenders(e2e):
    fixed = edit(load_example("ssr_list_detail"), _markup)
    repair = repair_script(fixed, "the result table and its labels were renamed")
    e2e.use_models(attach_script(e2e.template("ssr_list_detail")), repair)
    pid = await e2e.add("ssr")
    served = await e2e.get(f"/portals/{pid}/tenders")

    e2e.mock.state.variants["ssr"] = "markup"
    run_id = (await e2e.api.post(f"/portals/{pid}/refresh")).json()["runId"]
    store, scheduler = e2e.app.state.store, e2e.app.state.scheduler
    read = await store.claim_next_job(["read"], now=utcnow() + timedelta(days=1))
    await scheduler.run_job(read)

    portal = await e2e.get(f"/portals/{pid}")
    assert portal["state"] == "healing" and portal["reason"]
    assert await e2e.get(f"/portals/{pid}/tenders") == served  # the last correct tenders keep being served

    await e2e.drain()
    portal = await e2e.get(f"/portals/{pid}")
    assert portal["state"] == "serving", portal["reason"]
    assert "the result table and its labels were renamed" in portal["reason"]
    await assert_delivers(e2e, pid, "ssr", "markup")
    runs = await e2e.get(f"/portals/{pid}/runs")
    assert any(r["id"] == run_id for r in runs)
    assert runs[0]["result"] == "ok"


async def test_a_captcha_whose_documents_still_download_is_served(e2e_browser):
    # The model calls it blocked, but the template it tried already delivered: the captcha isn't a block.
    e = e2e_browser
    t = e.template("captcha_bypassable")
    e.use_models([[call("try_template", template=t)], [call("report_blocked", reason="The portal shows a captcha.")]])
    pid = await e.add("captcha")
    portal = await e.get(f"/portals/{pid}")
    assert portal["state"] == "serving", portal["reason"]
    await assert_delivers(e, pid, "captcha")


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("robot", "Documents sit behind a robot check (captcha) that we don't solve."),
        ("login", "Documents need a login (redirect to the login form)."),
    ],
)
async def test_a_blocked_portal_fails_with_the_reason(e2e, kind, reason):
    e2e.use_models([[call("detect_block", url=e2e.base + ENTRY[kind])], [call("report_blocked", reason=reason)]])
    pid = await e2e.add(kind)
    portal = await e2e.get(f"/portals/{pid}")
    assert portal["state"] == "failed"
    assert reason in portal["reason"]
    assert await e2e.get(f"/portals/{pid}/tenders") == []

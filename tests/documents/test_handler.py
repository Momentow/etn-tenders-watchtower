import hashlib

import httpx
import pytest

from watchtower.app import create_app
from watchtower.core.handlers import HANDLERS
from watchtower.core.models import TenderUpsert
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.documents.handler import DownloadIncomplete, enqueue_downloads, handle_download, run_download
from watchtower.documents.storage import FileStore
from watchtower.engine.result import DocumentRef
from watchtower.net import HostRateLimiter, PoliteClient

PDF = b"%PDF-1.4 spec"
XLSX = b"PK-not-really-a-zip? no: plain bytes"


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app(run_scheduler=False)
    async with app.router.lifespan_context(app):
        yield app


def portal_client(routes):
    def handler(request):
        status, body = routes.get(request.url.path, (404, b"no"))
        return httpx.Response(status, content=body)

    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler), max_attempts=1)


async def job_ctx(store, pid, refs):
    await enqueue_downloads(store, pid, "T1", [DocumentRef(url=u, fileName=n) for u, n in refs])
    job = await store.claim_next_job(["download"])
    return JobContext(store, StateMachine(store), job)


def test_download_handler_is_registered():
    assert HANDLERS["download"] is handle_download


async def test_download_job_stores_documents_and_api_serves_them(app, tmp_path):
    store = app.state.store
    portal, _ = await store.add_portal("https://portal.example/list")
    await store.upsert_tenders(portal.id, [TenderUpsert(tender_id="T1", data={"title": "x"}, content_hash="h")])

    client = portal_client({"/a.pdf": (200, PDF), "/b.xlsx": (200, XLSX)})
    ctx = await job_ctx(
        store, portal.id, [("https://portal.example/a.pdf", "Spec"), ("https://portal.example/b.xlsx", None)]
    )
    await run_download(ctx, client, FileStore())
    await client.aclose()

    tender = await store.get_tender(portal.id, "T1")
    assert [(d.file_name, d.sha256) for d in tender.documents] == [
        ("a.pdf", hashlib.sha256(PDF).hexdigest()),
        ("b.xlsx", hashlib.sha256(XLSX).hexdigest()),
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        [t] = (await api.get(f"/portals/{portal.id}/tenders")).json()
        assert t["documents"][0] == {
            "fileName": "a.pdf",
            "sizeBytes": len(PDF),
            "sha256": hashlib.sha256(PDF).hexdigest(),
            "path": f"/files/{hashlib.sha256(PDF).hexdigest()}",
        }
        r = await api.get(t["documents"][0]["path"])
        assert r.status_code == 200 and r.content == PDF


async def test_changed_documents_replace_old_ones_failed_ones_kept(app):
    store = app.state.store
    portal, _ = await store.add_portal("https://portal.example/list")
    await store.upsert_tenders(portal.id, [TenderUpsert(tender_id="T1", data={}, content_hash="h")])
    base = "https://portal.example"

    client = portal_client({"/a.pdf": (200, PDF), "/b.pdf": (200, b"%PDF b")})
    await run_download(
        await job_ctx(store, portal.id, [(f"{base}/a.pdf", None), (f"{base}/b.pdf", None)]), client, FileStore()
    )
    await client.aclose()

    # next read: a.pdf now fails, b.pdf was removed by the portal, c.pdf is new
    client = portal_client({"/a.pdf": (503, b""), "/c.pdf": (200, b"%PDF c")})
    ctx = await job_ctx(store, portal.id, [(f"{base}/a.pdf", None), (f"{base}/c.pdf", None)])
    with pytest.raises(DownloadIncomplete):  # the scheduler retries the job
        await run_download(ctx, client, FileStore())
    await client.aclose()

    names = sorted(d.file_name for d in (await store.get_tender(portal.id, "T1")).documents)
    assert names == ["a.pdf", "c.pdf"]  # a.pdf keeps its earlier copy, b.pdf is gone


async def test_tender_gone_means_nothing_to_do(app):
    store = app.state.store
    portal, _ = await store.add_portal("https://portal.example/list")
    client = portal_client({})
    ctx = await job_ctx(store, portal.id, [("https://portal.example/a.pdf", None)])
    await run_download(ctx, client, FileStore())  # no tender T1: returns without requests
    await client.aclose()

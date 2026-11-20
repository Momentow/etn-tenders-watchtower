"""Contract tests: every endpoint, status code and field per docs/spec/api-contract.md."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from watchtower.app import create_app
from watchtower.core.models import TenderUpsert

URL = "https://tenders.example.de/list"
OFFSET_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def store(app):
    return app.state.store


async def add(client, url=URL) -> str:
    r = await client.post("/portals", json={"url": url})
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------- POST /portals


async def test_post_portal_201_and_enqueues_attach(client, store):
    r = await client.post("/portals", json={"url": URL})
    assert r.status_code == 201
    assert set(r.json()) == {"id"}
    pid = r.json()["id"]

    [job] = await store.active_jobs(pid)
    assert job.kind == "attach" and job.payload["trigger"] == "attach"
    [run] = await store.list_runs(pid)
    assert run.kind == "read" and run.result is None and job.payload["run_id"] == run.id == job.run_id


async def test_same_address_same_id_no_second_attach(client, store):
    pid = await add(client)
    assert await add(client, "https://TENDERS.example.de/list/") == pid
    assert len(await store.active_jobs(pid)) == 1


@pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": "ftp://x.example/"}, {"link": URL}])
async def test_post_portal_rejects_bad_body(client, body):
    r = await client.post("/portals", json=body)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------- GET /portals, /portals/{id}


async def test_portal_entry_fields(client, store):
    pid = await add(client)
    r = await client.get("/portals")
    assert r.status_code == 200
    [entry] = r.json()
    assert entry == {
        "id": pid,
        "url": URL,
        "state": "integrating",
        "reason": entry["reason"],
        "tenderCount": 0,
        "lastSuccessAt": None,
    }
    assert isinstance(entry["reason"], str) and entry["reason"]

    run = await store.start_run(pid, "read")
    await store.finish_run(run.id, "ok")
    await store.upsert_tenders(pid, [TenderUpsert(tender_id="A", data={}, content_hash="h")])
    one = (await client.get(f"/portals/{pid}")).json()
    assert one["tenderCount"] == 1
    assert OFFSET_TIME.match(one["lastSuccessAt"])


async def test_empty_list_is_json_array(client):
    r = await client.get("/portals")
    assert r.status_code == 200 and r.json() == []


# ---------------------------------------------------------------- unknown ids → 404


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/portals/nope"),
        ("DELETE", "/portals/nope"),
        ("POST", "/portals/nope/refresh"),
        ("GET", "/portals/nope/tenders"),
        ("GET", "/portals/nope/runs"),
        ("GET", "/files/" + "0" * 64),
        ("GET", "/files/../../etc/passwd"),
    ],
)
async def test_unknown_ids_404(client, method, path):
    r = await client.request(method, path)
    assert r.status_code == 404


# ---------------------------------------------------------------- DELETE


async def test_delete_204_then_gone(client, store):
    pid = await add(client)
    r = await client.delete(f"/portals/{pid}")
    assert r.status_code == 204 and r.content == b""
    assert (await client.get(f"/portals/{pid}")).status_code == 404
    assert (await client.get("/portals")).json() == []
    assert (await client.delete(f"/portals/{pid}")).status_code == 404
    assert await store.active_jobs(pid) == []
    # the attach run reserved for the cancelled job is closed, so a re-add can attach again
    [run] = await store.list_runs(pid)
    assert run.result == "failed" and run.finished_at is not None

    assert await add(client) == pid
    assert [j.kind for j in await store.active_jobs(pid)] == ["attach"]


# ---------------------------------------------------------------- refresh


async def test_refresh_returns_running_run(client, store):
    pid = await add(client)
    [attach_run] = await store.list_runs(pid)
    # the attach is still running: refresh answers with its run, queues nothing new
    r = await client.post(f"/portals/{pid}/refresh")
    assert r.status_code == 202 and r.json() == {"runId": attach_run.id}
    assert len(await store.active_jobs(pid)) == 1


async def test_refresh_starts_a_read_and_repeats_return_it(client, store):
    pid = await add(client)
    [attach_run] = await store.list_runs(pid)
    await store.finish_run(attach_run.id, "ok")
    for job in await store.active_jobs(pid):
        await store.finish_job(job.id)
    await store.set_portal_state(pid, "serving", "Delivering.")

    first = (await client.post(f"/portals/{pid}/refresh")).json()["runId"]
    second = (await client.post(f"/portals/{pid}/refresh")).json()["runId"]
    assert first == second != attach_run.id
    [job] = await store.active_jobs(pid)
    assert job.kind == "read" and job.payload == {"run_id": first, "trigger": "refresh"}

    runs = (await client.get(f"/portals/{pid}/runs")).json()
    assert runs[0] == {
        "id": first,
        "kind": "read",
        "startedAt": runs[0]["startedAt"],
        "finishedAt": None,
        "result": None,
    }


async def test_refresh_on_failed_portal_starts_fresh_attach(client, store):
    pid = await add(client)
    [run] = await store.list_runs(pid)
    await store.finish_run(run.id, "failed")
    for job in await store.active_jobs(pid):
        await store.finish_job(job.id, ok=False)
    await store.set_portal_state(pid, "failed", "The portal requires a login.")

    r = await client.post(f"/portals/{pid}/refresh")
    assert r.status_code == 202
    [job] = await store.active_jobs(pid)
    assert job.kind == "attach" and job.run_id == r.json()["runId"]


# ---------------------------------------------------------------- runs


async def test_runs_fields_newest_first(client, store):
    pid = await add(client)
    [first] = await store.list_runs(pid)
    await store.finish_run(first.id, "ok")
    repair = await store.start_run(pid, "repair")
    runs = (await client.get(f"/portals/{pid}/runs")).json()
    assert [r["id"] for r in runs] == [repair.id, first.id]
    done = runs[1]
    assert set(done) == {"id", "kind", "startedAt", "finishedAt", "result"}
    assert done["kind"] == "read" and done["result"] == "ok"
    assert OFFSET_TIME.match(done["startedAt"]) and OFFSET_TIME.match(done["finishedAt"])
    assert runs[0]["kind"] == "repair" and runs[0]["result"] is None


# ---------------------------------------------------------------- tenders


FULL = {
    "url": "https://tenders.example.de/t/1",
    "title": "Road works",
    "buyerName": "Stadt Köln",
    "publicationDate": "2026-11-20",
    "submissionDeadline": "2026-12-09T10:00:00+01:00",
    "referenceNumber": "VG-2026-001",
    "description": "Resurfacing",
    "procedureType": "open",
    "contractNature": "works",
    "cpvCodes": ["45000000"],
    "placeOfPerformance": {"postalCode": "50667", "city": "Köln", "countryCode": "DE"},
}


async def test_tender_fields_required_null_optional_omitted(client, store):
    pid = await add(client)
    await store.upsert_tenders(
        pid,
        [
            TenderUpsert(tender_id="full", data=FULL, content_hash="a"),
            TenderUpsert(tender_id="sparse", data={"title": "Only a title", "cpvCodes": []}, content_hash="b"),
        ],
    )
    sha = "ab" * 32
    await store.add_document(pid, "full", source_url="https://x/d.pdf", file_name="spec.pdf", size_bytes=12, sha256=sha)
    r = await client.get(f"/portals/{pid}/tenders")
    assert r.status_code == 200
    full, sparse = r.json()

    assert full == {
        "id": "full",
        **FULL,
        "status": "open",
        "documents": [{"fileName": "spec.pdf", "sizeBytes": 12, "sha256": sha, "path": f"/files/{sha}"}],
        "lastChangedAt": full["lastChangedAt"],
    }
    assert OFFSET_TIME.match(full["lastChangedAt"])

    assert sparse == {
        "id": "sparse",
        "url": None,
        "title": "Only a title",
        "buyerName": None,
        "publicationDate": None,
        "submissionDeadline": None,
        "status": "open",
        "documents": [],
        "lastChangedAt": sparse["lastChangedAt"],
    }


async def test_unknown_data_keys_are_not_leaked(client, store):
    pid = await add(client)
    await store.upsert_tenders(
        pid, [TenderUpsert(tender_id="A", data={"title": "x", "_debug": "selector .foo"}, content_hash="a")]
    )
    [t] = (await client.get(f"/portals/{pid}/tenders")).json()
    assert "_debug" not in t


async def test_changed_since(client, store):
    pid = await add(client)
    t0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    await store.upsert_tenders(pid, [TenderUpsert(tender_id="old", data={}, content_hash="a")], now=t0)
    await store.upsert_tenders(
        pid, [TenderUpsert(tender_id="new", data={}, content_hash="b")], now=t0 + timedelta(hours=2)
    )

    async def ids(since: str, encode: bool = True):
        params = {"changedSince": since} if encode else None
        url = f"/portals/{pid}/tenders" + ("" if encode else f"?changedSince={since}")
        r = await client.get(url, params=params)
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()]

    assert await ids("2026-11-20T10:00:00+01:00") == ["new"]  # = 09:00 UTC, strictly later only
    assert await ids("2026-11-20T08:59:59Z") == ["old", "new"]
    assert await ids("2026-11-20T11:30:00+01:00", encode=False) == ["new"]  # raw "+" arrives as a space
    assert await ids("2026-11-20T12:00:00+00:00") == []


@pytest.mark.parametrize("bad", ["yesterday", "2026-11-20T10:00:00"])
async def test_changed_since_rejects_bad_values(client, bad):
    pid = await add(client)
    r = await client.get(f"/portals/{pid}/tenders", params={"changedSince": bad})
    assert r.status_code == 400


# ---------------------------------------------------------------- files


async def test_files_serves_bytes(client, store, tmp_path):
    pid = await add(client)
    await store.upsert_tenders(pid, [TenderUpsert(tender_id="A", data={}, content_hash="a")])
    content = b"%PDF-1.4 tender spec"
    sha = hashlib.sha256(content).hexdigest()
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / sha).write_bytes(content)
    await store.add_document(
        pid, "A", source_url="https://x/s", file_name="Leistungs verz.pdf", size_bytes=len(content), sha256=sha
    )

    [t] = (await client.get(f"/portals/{pid}/tenders")).json()
    r = await client.get(t["documents"][0]["path"])
    assert r.status_code == 200 and r.content == content
    assert r.headers["content-type"] == "application/pdf"
    assert "Leistungs" in r.headers["content-disposition"]


async def test_file_known_but_missing_on_disk_is_404(client, store):
    pid = await add(client)
    await store.upsert_tenders(pid, [TenderUpsert(tender_id="A", data={}, content_hash="a")])
    await store.add_document(pid, "A", source_url="u", file_name="f.pdf", size_bytes=1, sha256="cd" * 32)
    assert (await client.get("/files/" + "cd" * 32)).status_code == 404


# ---------------------------------------------------------------- shared routes


async def test_dashboard_still_at_root_and_docs_path_free(client):
    r = await client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert (await client.get("/docs")).status_code == 404  # reserved for the docs site (T-019)

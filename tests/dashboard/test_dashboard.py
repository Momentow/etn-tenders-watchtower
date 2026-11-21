"""Dashboard (T-018): overview, portal detail, htmx partials, and that it stays read-only."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from watchtower.app import create_app
from watchtower.core.models import Run, TenderUpsert
from watchtower.core.state_machine import StateMachine
from watchtower.dashboard import views

URL = "https://tenders.example.de/list"


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app(run_scheduler=False)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def store(app):
    return app.state.store


async def serving_portal(store) -> str:
    """A portal that attached, then broke and was repaired: one ok read, one failed read, one ok repair."""
    portal, _ = await store.add_portal(URL)
    sm = StateMachine(store)
    run, _ = await store.request_run(portal.id, "attach", trigger="attach")
    tv = await store.add_template_version(portal.id, {"list": {"kind": "html"}}, notes="first attach")
    await store.promote_template_version(portal.id, tv.version)
    await store.upsert_tenders(
        portal.id,
        [
            TenderUpsert(
                tender_id="T-1",
                data={
                    "url": "https://tenders.example.de/t/1",
                    "title": "Road resurfacing <Nord>",
                    "buyerName": "Stadt Beispiel",
                    "publicationDate": "2026-11-01",
                    "submissionDeadline": "2026-12-09T10:00:00+01:00",
                },
                content_hash="h1",
            ),
            TenderUpsert(tender_id="T-2", data={"url": "javascript:alert(1)", "title": "Bad link"}, content_hash="h2"),
        ],
    )
    await store.add_document(
        portal.id, "T-1", source_url="https://x/a.pdf", file_name="spec.pdf", size_bytes=2048, sha256="a" * 64
    )
    await store.finish_run(run.id, "ok", tender_count=2)
    await sm.apply(portal.id, "attach_succeeded", tender_count=2)

    read, _ = await store.request_run(portal.id, "read", trigger="scheduled")
    await store.finish_run(read.id, "failed", details={"problems": ["Zero tenders where the last reads had 2."]})
    await sm.apply(portal.id, "read_failed", detail="zero tenders")
    repair, _ = await store.request_run(portal.id, "investigate", trigger="failure")
    diagnosis = {"kind": "Changed", "reason": "new markup"}
    await store.finish_run(repair.id, "ok", tender_count=2, details={"diagnosis": diagnosis})
    await sm.apply(portal.id, "repair_succeeded", detail="new markup", tender_count=2)
    return portal.id


async def test_empty_overview(client):
    r = await client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "No portals yet" in r.text
    assert "/dashboard/static/htmx.min.js" in r.text
    assert 'hx-get="/dashboard/partials/portals"' in r.text


async def test_overview_lists_portal_with_state_reason_count(client, store):
    pid = await serving_portal(store)
    r = await client.get("/")
    assert URL in r.text and f"/dashboard/portals/{pid}" in r.text
    assert 'class="badge serving"' in r.text
    assert "Repaired: new markup" in r.text  # reason
    assert "Repaired" in r.text  # last activity

    partial = await client.get("/dashboard/partials/portals")
    assert partial.status_code == 200
    assert "<html" not in partial.text and URL in partial.text


async def test_portal_detail_shows_timeline_templates_tenders(client, store):
    pid = await serving_portal(store)
    r = await client.get(f"/dashboard/portals/{pid}")
    assert r.status_code == 200
    text = r.text
    # run timeline: attach, failed scheduled read with its problem, the repair with its diagnosis
    assert "Attach" in text and "Scheduled read" in text and "Repair" in text
    assert "Zero tenders where the last reads had 2." in text
    assert "Changed: new markup" in text
    # template versions
    assert "v1" in text and "first attach" in text and f"/dashboard/portals/{pid}/templates/1.json" in text
    # tenders, escaped, with documents
    assert "Road resurfacing &lt;Nord&gt;" in text
    assert "spec.pdf" in text and f"/files/{'a' * 64}" in text
    # unsafe portal link is not made clickable
    assert "javascript:alert" not in text

    live = await client.get(f"/dashboard/partials/portals/{pid}/live")
    assert live.status_code == 200 and "Run timeline" in live.text
    data = await client.get(f"/dashboard/partials/portals/{pid}/data")
    assert data.status_code == 200 and "Template versions" in data.text

    tj = await client.get(f"/dashboard/portals/{pid}/templates/1.json")
    assert tj.json() == {"list": {"kind": "html"}}
    assert (await client.get(f"/dashboard/portals/{pid}/templates/9.json")).status_code == 404


async def test_unknown_or_deleted_portal_404(client, store):
    assert (await client.get("/dashboard/portals/nope")).status_code == 404
    assert (await client.get("/dashboard/partials/portals/nope/live")).status_code == 404
    pid = await serving_portal(store)
    await store.delete_portal(pid)
    assert (await client.get(f"/dashboard/portals/{pid}")).status_code == 404
    assert URL not in (await client.get("/")).text


async def test_running_run_is_shown(client, store):
    portal, _ = await store.add_portal(URL)
    await store.request_run(portal.id, "attach", trigger="attach")
    r = await client.get("/")
    assert "Attaching now" in r.text


async def test_static_assets(client):
    js = await client.get("/dashboard/static/htmx.min.js")
    assert js.status_code == 200 and "htmx" in js.text
    assert (await client.get("/dashboard/static/dashboard.css")).status_code == 200
    assert (await client.get("/dashboard/static/..%2Fviews.py")).status_code == 404


async def test_dashboard_never_writes(client, store):
    pid = await serving_portal(store)
    before = (await store.get_portal(pid), await store.list_runs(pid), await store.active_jobs(pid))
    for path in ("/", "/dashboard/partials/portals", f"/dashboard/portals/{pid}"):
        await client.get(path)
    after = (await store.get_portal(pid), await store.list_runs(pid), await store.active_jobs(pid))
    assert before == after


def test_run_view_labels_and_details():
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)

    def run(kind, details, result="ok"):
        return Run(
            id="r", portal_id="p", kind=kind, started_at=now, finished_at=now + timedelta(seconds=75),
            result=result, template_version=None, tender_count=None, details=details, snapshot=None,
        )  # fmt: skip

    audit = views.run_view(
        run(
            "read",
            {
                "trigger": "audit",
                "audit": True,
                "stats": {"requests": 9},
                "promoted": 3,
                "diagnosis": "healthy",
                "diagnosis_reason": "all 10 tender links were delivered",
                "tenders": {"new": 2, "changed": 0, "closed": 1},
                "template_version": 2,
            },
        )
    )
    assert audit.label == "Health audit" and audit.template_version == 2
    assert audit.extra == [("tenders", "2 new, 1 closed"), ("promoted", "3")]
    assert audit.diagnosis == "Healthy: all 10 tender links were delivered"
    assert views.run_view(run("repair", {"trigger": "audit_finding"})).label == "Repair after audit"
    assert views.duration(audit.duration_seconds) == "1 min 15 s"
    cancelled = views.run_view(run("read", {"trigger": "refresh", "cancelled": "portal deleted"}, "failed"))
    assert cancelled.error == "Cancelled: portal deleted" and cancelled.status == "failed"
    assert views.run_view(run("repair", {"diagnosis": "Transient: timeout"})).diagnosis == "Transient: timeout"


def test_formatters():
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    assert views.ago(None) == "never"
    assert views.ago(now - timedelta(seconds=3), now) == "just now"
    assert views.ago(now - timedelta(minutes=5), now) == "5 min ago"
    assert views.ago(now - timedelta(days=2), now) == "2 d ago"
    assert views.safe_href("https://a.b") == "https://a.b" and views.safe_href("javascript:x") is None
    assert views.filesize(2048) == "2.0 KB" and views.filesize(12) == "12 B"


async def test_health_checks_and_audits_are_shown(client, store, app):
    pid = await serving_portal(store)
    r = await client.get("/")
    assert "Proactive health checks" in r.text and "every 24 h" in r.text
    assert "none yet" in r.text and "next audit in" in r.text  # first audit staggered into the future

    run, _ = await store.request_run(pid, "investigate", trigger="audit", run_kind="read")
    await store.finish_run(
        run.id, "ok", tender_count=2,
        details={"audit": True, "diagnosis": "healthy", "diagnosis_reason": "all 2 tender links were delivered"},
    )  # fmt: skip
    detail = await client.get(f"/dashboard/portals/{pid}")
    assert "Health audit" in detail.text and "Healthy: all 2 tender links were delivered" in detail.text
    assert 'class="badge ok">healthy' in detail.text

    app.state.scheduler.healthcheck.mode = "reactive"
    r = await client.get("/")
    assert "Reactive health checks" in r.text and "next audit" not in r.text


async def test_model_cost_per_portal_and_scenario(client, store):
    # T-099: model usage stored by the pipeline shows on the overview and the portal page.
    pid = await serving_portal(store)
    run = (await store.list_runs(pid))[-1]
    row = dict(scenario="attach", tier="strong", model="gpt-5.6-luna", requests=12, input_tokens=180_000)
    await store.add_model_usage(
        pid,
        run.id,
        [
            {**row, "cached_tokens": 120_000, "output_tokens": 4_000, "cost_usd": 0.0168},
            {**row, "tier": "light", "requests": 5, "input_tokens": 20_000, "cached_tokens": 0, "output_tokens": 2_000,
             "cost_usd": 0.0064},
        ],
    )  # fmt: skip
    overview = (await client.get("/")).text
    assert "Model cost" in overview and "$0.0232" in overview and "17 calls" in overview
    page = (await client.get(f"/dashboard/portals/{pid}")).text
    assert "Model usage" in page and "gpt-5.6-luna" in page and "$0.0168" in page and "180.0k (120.0k)" in page


async def test_a_model_without_a_price_shows_as_unknown(client, store):
    pid = await serving_portal(store)
    u = dict(scenario="investigate", tier="strong", model="mystery", requests=1, input_tokens=10, cached_tokens=0)
    await store.add_model_usage(pid, None, [{**u, "output_tokens": 1, "cost_usd": None}])
    assert "price unknown" in (await client.get("/")).text
    assert views.usd(0.00004) == "<$0.0001" and views.usd(2.5) == "$2.50"

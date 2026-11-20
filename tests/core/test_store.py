from datetime import UTC, datetime, timedelta, timezone

import pytest

from watchtower.core.migrations import MIGRATIONS
from watchtower.core.models import TenderUpsert
from watchtower.core.store import NotFound, Store

URL = "https://tenders.example.de/list"


@pytest.fixture
async def store(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    yield s
    await s.close()


@pytest.fixture
async def portal(store):
    p, _ = await store.add_portal(URL)
    return p


def t(tid, h="h1", status="open", **data):
    return TenderUpsert(tender_id=tid, data={"title": f"Tender {tid}", **data}, content_hash=h, status=status)


# ---------------------------------------------------------------- migrations


async def test_migrations_applied_with_wal(tmp_path):
    s = await Store.open(tmp_path / "sub" / "w.db")
    try:
        assert await s.schema_version() == len(MIGRATIONS)
        async with s._tx() as db:
            (mode,) = await (await db.execute("PRAGMA journal_mode")).fetchone()
        assert mode == "wal"
    finally:
        await s.close()


async def test_reopen_keeps_data_and_does_not_reapply(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    p, _ = await s.add_portal(URL)
    await s.close()
    s = await Store.open(tmp_path / "w.db")
    try:
        assert (await s.get_portal(p.id)).url == URL
        assert await s.schema_version() == len(MIGRATIONS)
    finally:
        await s.close()


# ---------------------------------------------------------------- portals


async def test_add_portal_is_idempotent_per_address(store):
    p1, created1 = await store.add_portal(URL)
    p2, created2 = await store.add_portal("https://TENDERS.example.de/list/")
    assert created1 and not created2
    assert p1.id == p2.id
    assert p1.state == "integrating" and p1.reason
    assert p1.tender_count == 0 and p1.last_success_at is None
    assert len(await store.list_portals()) == 1


async def test_set_state_and_unknown_portal(store, portal):
    p = await store.set_portal_state(portal.id, "failed", "The portal requires a login.")
    assert (p.state, p.reason) == ("failed", "The portal requires a login.")
    with pytest.raises(NotFound):
        await store.set_portal_state("nope", "serving", "x")
    assert await store.get_portal("nope") is None


async def test_delete_hides_portal_cancels_jobs_and_readd_revives(store, portal):
    queued = await store.enqueue_job("read", portal_id=portal.id)
    assert await store.delete_portal(portal.id)
    assert not await store.delete_portal(portal.id)
    assert await store.get_portal(portal.id) is None
    assert await store.get_portal(portal.id, include_deleted=True) is not None
    assert await store.list_portals() == []
    assert await store.is_portal_deleted(portal.id)
    assert await store.is_portal_deleted("unknown")
    assert await store.claim_next_job() is None
    assert (await store.active_jobs(portal.id)) == []
    assert queued.status == "queued"

    p, created = await store.add_portal(URL)
    assert created and p.id == portal.id and p.state == "integrating"
    assert not await store.is_portal_deleted(portal.id)


# ---------------------------------------------------------------- runs


async def test_run_lifecycle_sets_last_success(store, portal):
    run = await store.start_run(portal.id, "read", details={"trigger": "attach"})
    assert run.result is None and run.finished_at is None
    assert (await store.get_running_run(portal.id, "read")).id == run.id

    done = await store.finish_run(run.id, "ok", tender_count=7, details={"pages": 2}, snapshot={"outline": ["ul>li"]})
    assert done.result == "ok" and done.finished_at is not None
    assert done.details == {"trigger": "attach", "pages": 2}
    assert done.tender_count == 7
    assert await store.get_running_run(portal.id) is None
    assert (await store.get_portal(portal.id)).last_success_at == done.finished_at


async def test_failed_run_does_not_touch_last_success(store, portal):
    run = await store.start_run(portal.id, "repair")
    await store.finish_run(run.id, "failed", details={"problems": ["zero tenders"]})
    assert (await store.get_portal(portal.id)).last_success_at is None
    with pytest.raises(NotFound):
        await store.finish_run("missing", "ok")


async def test_list_runs_newest_first_and_last_good_snapshot(store, portal):
    r1 = await store.start_run(portal.id, "read")
    await store.finish_run(r1.id, "ok", snapshot={"v": 1})
    r2 = await store.start_run(portal.id, "read")
    await store.finish_run(r2.id, "failed", snapshot={"v": 2})
    r3 = await store.start_run(portal.id, "read")
    runs = await store.list_runs(portal.id)
    assert [r.id for r in runs] == [r3.id, r2.id, r1.id]
    assert [r.id for r in await store.list_runs(portal.id, limit=1)] == [r3.id]
    assert await store.last_good_snapshot(portal.id) == {"v": 1}


# ---------------------------------------------------------------- templates


async def test_template_versions_promote_only_explicitly(store, portal):
    v1 = await store.add_template_version(portal.id, {"list": "css:.a"}, notes="attach")
    assert v1.version == 1 and v1.promoted_at is None
    assert await store.get_current_template(portal.id) is None

    await store.promote_template_version(portal.id, 1)
    v2 = await store.add_template_version(portal.id, {"list": "css:.b"})
    assert v2.version == 2
    current = await store.get_current_template(portal.id)
    assert current.version == 1 and current.template == {"list": "css:.a"}

    await store.promote_template_version(portal.id, 2)
    assert (await store.get_current_template(portal.id)).version == 2
    assert (await store.get_portal(portal.id)).current_template_version == 2
    assert [v.version for v in await store.list_template_versions(portal.id)] == [2, 1]
    with pytest.raises(NotFound):
        await store.promote_template_version(portal.id, 9)


# ---------------------------------------------------------------- tenders


async def test_upsert_tracks_changes(store, portal):
    t0 = datetime(2026, 11, 20, 10, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    s = await store.upsert_tenders(portal.id, [t("A"), t("B")], now=t0)
    assert s.new == ["A", "B"] and not s.changed

    s = await store.upsert_tenders(portal.id, [t("A"), t("B", h="h2")], now=t1)
    assert s.unchanged == ["A"] and s.changed == ["B"]

    s = await store.upsert_tenders(portal.id, [t("A", status="withdrawn")], now=t2)
    assert s.changed == ["A"]

    tenders = {x.tender_id: x for x in await store.list_tenders(portal.id)}
    assert tenders["A"].last_changed_at == t2 and tenders["A"].status == "withdrawn"
    assert tenders["A"].last_seen_at == t2
    assert tenders["B"].last_changed_at == t1 and tenders["B"].content_hash == "h2"
    assert tenders["B"].first_seen_at == t0
    assert (await store.get_portal(portal.id)).tender_count == 2


async def test_changed_since_is_strict_and_offset_aware(store, portal):
    t0 = datetime(2026, 11, 20, 10, tzinfo=UTC)
    await store.upsert_tenders(portal.id, [t("A")], now=t0)
    await store.upsert_tenders(portal.id, [t("B")], now=t0 + timedelta(minutes=5))

    assert [x.tender_id for x in await store.list_tenders(portal.id, changed_since=t0)] == ["B"]
    cet = timezone(timedelta(hours=1))
    since = datetime(2026, 11, 20, 10, 59, tzinfo=cet)  # 09:59 UTC
    assert [x.tender_id for x in await store.list_tenders(portal.id, changed_since=since)] == ["A", "B"]


async def test_close_unlisted_tenders(store, portal):
    t0 = datetime(2026, 11, 20, 10, tzinfo=UTC)
    await store.upsert_tenders(portal.id, [t("A"), t("B"), t("C", status="withdrawn")], now=t0)
    closed = await store.close_unlisted_tenders(portal.id, ["A"], now=t0 + timedelta(hours=1))
    assert closed == ["B"]  # withdrawn stays withdrawn
    b = await store.get_tender(portal.id, "B")
    assert b.status == "closed" and b.last_changed_at == t0 + timedelta(hours=1)


async def test_naive_datetime_rejected(store, portal):
    with pytest.raises(ValueError):
        await store.upsert_tenders(portal.id, [t("A")], now=datetime(2026, 11, 20, 10))


# ---------------------------------------------------------------- documents


async def test_documents_attach_to_tenders_and_files_by_sha(store, portal):
    await store.upsert_tenders(portal.id, [t("A")])
    d1 = await store.add_document(
        portal.id,
        "A",
        source_url="https://x/docs.zip",
        archive_member="spec.pdf",
        file_name="spec.pdf",
        size_bytes=10,
        sha256="ab" * 32,
    )
    await store.add_document(
        portal.id,
        "A",
        source_url="https://x/docs.zip",
        archive_member="spec.pdf",
        file_name="spec-v2.pdf",
        size_bytes=11,
        sha256="cd" * 32,
    )
    await store.add_document(
        portal.id,
        "A",
        source_url="https://x/docs.zip",
        archive_member="price.xlsx",
        file_name="price.xlsx",
        size_bytes=5,
        sha256="ef" * 32,
    )
    docs = (await store.get_tender(portal.id, "A")).documents
    assert [d.file_name for d in docs] == ["spec-v2.pdf", "price.xlsx"]
    assert docs[0].id == d1.id and docs[0].path == "/files/" + "cd" * 32
    assert (await store.list_tenders(portal.id))[0].documents == docs
    assert (await store.get_file("ef" * 32)).file_name == "price.xlsx"
    assert await store.get_file("00" * 32) is None


async def test_document_requires_known_tender(store, portal):
    with pytest.raises(Exception):  # noqa: B017 — sqlite IntegrityError via aiosqlite
        await store.add_document(portal.id, "nope", source_url="u", file_name="f", size_bytes=1, sha256="0" * 64)


# ---------------------------------------------------------------- jobs


async def test_job_queue_claim_finish_retry(store, portal):
    now = datetime.now(UTC)
    j1 = await store.enqueue_job("attach", portal_id=portal.id, payload={"url": URL})
    later = await store.enqueue_job("read", portal_id=portal.id, run_after=now + timedelta(hours=1))
    await store.enqueue_job("download", portal_id=portal.id)

    claimed = await store.claim_next_job(["attach", "read"])
    assert claimed.id == j1.id and claimed.status == "running" and claimed.attempts == 1
    assert claimed.payload == {"url": URL}
    assert await store.claim_next_job(["attach", "read"]) is None  # `read` not due yet
    assert (await store.claim_next_job(["read"], now=now + timedelta(hours=2))).id == later.id

    retried = await store.retry_job(j1.id, run_after=now - timedelta(seconds=1), error="timeout")
    assert retried.status == "queued" and retried.error == "timeout"
    again = await store.claim_next_job(["attach"])
    assert again.id == j1.id and again.attempts == 2

    await store.set_job_run(j1.id, "run123")
    finished = await store.finish_job(j1.id, ok=False, error="blocked")
    assert finished.status == "failed" and finished.run_id == "run123" and finished.finished_at

    assert [j.kind for j in await store.active_jobs(portal.id)] == ["read", "download"]
    assert [j.kind for j in await store.active_jobs(portal.id, ["download"])] == ["download"]


async def test_running_jobs_resume_after_restart(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    p, _ = await s.add_portal(URL)
    await s.enqueue_job("read", portal_id=p.id)
    await s.claim_next_job()
    await s.close()

    s = await Store.open(tmp_path / "w.db")
    try:
        assert await s.requeue_running_jobs() == 1
        job = await s.claim_next_job()
        assert job.kind == "read" and job.attempts == 2
    finally:
        await s.close()

"""`core.store`: the only database access layer (ADR 0002, ADR 0004).

One aiosqlite connection per process, WAL mode, every operation serialised by an asyncio lock and run in its own
transaction. Policy (when to change state, what counts as a failed read) lives in the callers: the state machine,
scheduler and pipeline. The store only records it.
"""

import asyncio
import json
import sqlite3
import uuid
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from watchtower.core.migrations import MIGRATIONS
from watchtower.core.models import (
    DocumentFile,
    Job,
    JobKind,
    Portal,
    PortalState,
    Run,
    RunKind,
    RunResult,
    StoredTender,
    TemplateVersion,
    TenderUpsert,
    UpsertSummary,
)
from watchtower.core.times import from_db, to_db, utcnow
from watchtower.core.urls import normalize_url, portal_id_for

INITIAL_REASON = "Attaching the portal: learning how to read it."

_PORTAL_SELECT = """
    SELECT p.*, (SELECT COUNT(*) FROM tenders t WHERE t.portal_id = p.id) AS tender_count
    FROM portals p
"""


class NotFound(LookupError):
    pass


class Store:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    async def open(cls, path: str | Path) -> "Store":
        """Open (creating if needed) the database at `path` and apply pending migrations. ":memory:" works for tests."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=NORMAL")
        store = cls(conn)
        await store._migrate()
        return store

    async def close(self) -> None:
        await self._conn.close()

    async def _migrate(self) -> None:
        async with self._tx() as db:
            (applied,) = await (await db.execute("PRAGMA user_version")).fetchone()
            for number, script in enumerate(MIGRATIONS[applied:], start=applied + 1):
                for statement in _split_sql(script):
                    await db.execute(statement)
                await db.execute(f"PRAGMA user_version = {number}")

    async def schema_version(self) -> int:
        async with self._tx() as db:
            (version,) = await (await db.execute("PRAGMA user_version")).fetchone()
            return version

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            await self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ portals

    async def add_portal(self, url: str) -> tuple[Portal, bool]:
        """Return the portal for `url`, creating it if new. `created` is True for a new or re-added (deleted) portal.

        A re-added portal starts over in `integrating`; its old runs, templates and tenders are kept.
        """
        normalized = normalize_url(url)
        portal_id = portal_id_for(url)
        now = to_db(utcnow())
        async with self._tx() as db:
            row = await _one(db, "SELECT id, deleted_at FROM portals WHERE normalized_url = ?", normalized)
            if row is None:
                await db.execute(
                    "INSERT INTO portals (id, url, normalized_url, state, reason, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'integrating', ?, ?, ?)",
                    (portal_id, url.strip(), normalized, INITIAL_REASON, now, now),
                )
                created = True
            elif row["deleted_at"] is not None:
                await db.execute(
                    "UPDATE portals SET deleted_at = NULL, state = 'integrating', reason = ?, updated_at = ?"
                    " WHERE id = ?",
                    (INITIAL_REASON, now, row["id"]),
                )
                portal_id, created = row["id"], True
            else:
                portal_id, created = row["id"], False
            return await self._portal(db, portal_id), created

    async def get_portal(self, portal_id: str, *, include_deleted: bool = False) -> Portal | None:
        async with self._tx() as db:
            portal = await self._portal(db, portal_id)
        if portal is None or (portal.deleted_at is not None and not include_deleted):
            return None
        return portal

    async def list_portals(self, *, include_deleted: bool = False) -> list[Portal]:
        where = "" if include_deleted else " WHERE p.deleted_at IS NULL"
        async with self._tx() as db:
            rows = await _all(db, _PORTAL_SELECT + where + " ORDER BY p.created_at, p.id")
        return [_portal(r) for r in rows]

    async def set_portal_state(self, portal_id: str, state: PortalState, reason: str) -> Portal:
        """Write `state`/`reason`. Only the state machine (T-006) calls this."""
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE portals SET state = ?, reason = ?, updated_at = ? WHERE id = ?",
                (state, reason, to_db(utcnow()), portal_id),
            )
            if cur.rowcount == 0:
                raise NotFound(f"portal {portal_id}")
            return await self._portal(db, portal_id)

    async def delete_portal(self, portal_id: str) -> bool:
        """Mark the portal deleted and cancel its queued jobs. False if it doesn't exist or is already deleted."""
        now = to_db(utcnow())
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE portals SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now, now, portal_id),
            )
            if cur.rowcount == 0:
                return False
            # Runs reserved by queued jobs (request_run) would otherwise stay unfinished forever.
            await db.execute(
                "UPDATE runs SET finished_at = ?, result = 'failed', details = json_set(details, '$.cancelled',"
                " 'portal deleted') WHERE id IN (SELECT run_id FROM jobs WHERE portal_id = ? AND status = 'queued'"
                " AND run_id IS NOT NULL)",
                (now, portal_id),
            )
            await db.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ?, error = 'portal deleted'"
                " WHERE portal_id = ? AND status = 'queued'",
                (now, portal_id),
            )
            return True

    async def is_portal_deleted(self, portal_id: str) -> bool:
        """Workers call this before each portal request; True also for unknown ids."""
        async with self._tx() as db:
            row = await _one(db, "SELECT deleted_at FROM portals WHERE id = ?", portal_id)
        return row is None or row["deleted_at"] is not None

    async def _portal(self, db: aiosqlite.Connection, portal_id: str) -> Portal | None:
        row = await _one(db, _PORTAL_SELECT + " WHERE p.id = ?", portal_id)
        return None if row is None else _portal(row)

    # ------------------------------------------------------------------ runs

    async def start_run(
        self,
        portal_id: str,
        kind: RunKind,
        *,
        details: dict[str, Any] | None = None,
        template_version: int | None = None,
    ) -> Run:
        run_id = uuid.uuid4().hex
        async with self._tx() as db:
            await db.execute(
                "INSERT INTO runs (id, portal_id, kind, started_at, template_version, details)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, portal_id, kind, to_db(utcnow()), template_version, json.dumps(details or {})),
            )
            return _run(await _one(db, "SELECT * FROM runs WHERE id = ?", run_id))

    async def finish_run(
        self,
        run_id: str,
        result: RunResult,
        *,
        tender_count: int | None = None,
        details: dict[str, Any] | None = None,
        snapshot: Any | None = None,
    ) -> Run:
        """Close a run. `details` is merged into the existing details. An `ok` run sets the portal's `lastSuccessAt`."""
        now = to_db(utcnow())
        async with self._tx() as db:
            row = await _one(db, "SELECT * FROM runs WHERE id = ?", run_id)
            if row is None:
                raise NotFound(f"run {run_id}")
            merged = {**json.loads(row["details"]), **(details or {})}
            await db.execute(
                "UPDATE runs SET finished_at = ?, result = ?, tender_count = ?, details = ?, snapshot = ? WHERE id = ?",
                (
                    now,
                    result,
                    tender_count,
                    json.dumps(merged),
                    None if snapshot is None else json.dumps(snapshot),
                    run_id,
                ),
            )
            if result == "ok":
                await db.execute(
                    "UPDATE portals SET last_success_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["portal_id"]),
                )
            return _run(await _one(db, "SELECT * FROM runs WHERE id = ?", run_id))

    async def get_run(self, run_id: str) -> Run | None:
        async with self._tx() as db:
            row = await _one(db, "SELECT * FROM runs WHERE id = ?", run_id)
        return None if row is None else _run(row)

    async def list_runs(self, portal_id: str, *, limit: int | None = None) -> list[Run]:
        """Newest first."""
        sql = "SELECT * FROM runs WHERE portal_id = ? ORDER BY started_at DESC, rowid DESC"
        params: tuple = (portal_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        async with self._tx() as db:
            return [_run(r) for r in await _all(db, sql, *params)]

    async def get_running_run(self, portal_id: str, kind: RunKind | None = None) -> Run | None:
        """The newest unfinished run, e.g. for `refresh` to return the running read's `runId`."""
        sql = "SELECT * FROM runs WHERE portal_id = ? AND finished_at IS NULL"
        params: tuple = (portal_id,)
        if kind is not None:
            sql += " AND kind = ?"
            params += (kind,)
        sql += " ORDER BY started_at DESC, rowid DESC LIMIT 1"
        async with self._tx() as db:
            row = await _one(db, sql, *params)
        return None if row is None else _run(row)

    async def request_run(
        self,
        portal_id: str,
        job_kind: Literal["attach", "read", "investigate"],
        *,
        trigger: str,
        run_kind: RunKind | None = None,
    ) -> tuple[Run, bool]:
        """Ask for work on a portal and get the run that will record it, atomically (ADR 0005).

        If the portal already has an unfinished run (read or repair), that run is returned with `created=False` and
        nothing is queued: one active attach/read/investigate per portal. Otherwise a run is started (`attach`/`read`
        → `read` run, `investigate` → `repair` run) and a job of `job_kind` is queued with `payload.run_id` set; the
        worker that takes the job finishes that run. `run_kind` overrides the mapping: an audit is an `investigate`
        job recorded as a `read` run (ADR 0002).
        """
        run_kind = run_kind or ("repair" if job_kind == "investigate" else "read")
        now = utcnow()
        async with self._tx() as db:
            row = await _one(
                db,
                "SELECT * FROM runs WHERE portal_id = ? AND finished_at IS NULL ORDER BY started_at DESC, rowid DESC"
                " LIMIT 1",
                portal_id,
            )
            if row is not None:
                return _run(row), False
            run_id = uuid.uuid4().hex
            await db.execute(
                "INSERT INTO runs (id, portal_id, kind, started_at, details) VALUES (?, ?, ?, ?, ?)",
                (run_id, portal_id, run_kind, to_db(now), json.dumps({"trigger": trigger})),
            )
            await db.execute(
                "INSERT INTO jobs (kind, portal_id, payload, status, run_after, created_at, run_id)"
                " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (
                    job_kind,
                    portal_id,
                    json.dumps({"run_id": run_id, "trigger": trigger}),
                    to_db(now),
                    to_db(now),
                    run_id,
                ),
            )
            return _run(await _one(db, "SELECT * FROM runs WHERE id = ?", run_id)), True

    async def schedule_info(self) -> list[dict[str, Any]]:
        """Per live portal: `portal_id`, `state`, `created_at`, `last_read_at` (newest finished read run),
        `last_audit_at` (newest audit run start) and `busy` (has an unfinished run). For the scheduler's ticks."""
        async with self._tx() as db:
            rows = await _all(
                db,
                """
                SELECT p.id AS portal_id, p.state, p.created_at,
                    (SELECT MAX(finished_at) FROM runs r WHERE r.portal_id = p.id AND r.kind = 'read') AS last_read_at,
                    (SELECT MAX(started_at) FROM runs r WHERE r.portal_id = p.id
                        AND json_extract(r.details, '$.trigger') = 'audit') AS last_audit_at,
                    EXISTS (SELECT 1 FROM runs r WHERE r.portal_id = p.id AND r.finished_at IS NULL) AS busy
                FROM portals p WHERE p.deleted_at IS NULL ORDER BY p.created_at, p.id
                """,
            )
        return [
            {
                "portal_id": r["portal_id"],
                "state": r["state"],
                "created_at": from_db(r["created_at"]),
                "last_read_at": from_db(r["last_read_at"]),
                "last_audit_at": from_db(r["last_audit_at"]),
                "busy": bool(r["busy"]),
            }
            for r in rows
        ]

    async def last_good_snapshot(self, portal_id: str) -> Any | None:
        """Snapshot from the newest `ok` run that stored one (fed to the repair diff tool)."""
        async with self._tx() as db:
            row = await _one(
                db,
                "SELECT snapshot FROM runs WHERE portal_id = ? AND result = 'ok' AND snapshot IS NOT NULL"
                " ORDER BY started_at DESC, rowid DESC LIMIT 1",
                portal_id,
            )
        return None if row is None else json.loads(row["snapshot"])

    # ------------------------------------------------------------------ templates

    async def add_template_version(self, portal_id: str, template: dict[str, Any], notes: str = "") -> TemplateVersion:
        """Store a candidate template as the next version. It's not used until promoted."""
        async with self._tx() as db:
            (latest,) = await (
                await db.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM template_versions WHERE portal_id = ?", (portal_id,)
                )
            ).fetchone()
            await db.execute(
                "INSERT INTO template_versions (portal_id, version, template, notes, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (portal_id, latest + 1, json.dumps(template), notes, to_db(utcnow())),
            )
            return await self._template(db, portal_id, latest + 1)

    async def promote_template_version(self, portal_id: str, version: int) -> TemplateVersion:
        """Make `version` the portal's current template. Call only after it passed validation."""
        now = to_db(utcnow())
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE template_versions SET promoted_at = ? WHERE portal_id = ? AND version = ?",
                (now, portal_id, version),
            )
            if cur.rowcount == 0:
                raise NotFound(f"template {portal_id} v{version}")
            await db.execute(
                "UPDATE portals SET current_template_version = ?, updated_at = ? WHERE id = ?",
                (version, now, portal_id),
            )
            return await self._template(db, portal_id, version)

    async def get_current_template(self, portal_id: str) -> TemplateVersion | None:
        async with self._tx() as db:
            row = await _one(db, "SELECT current_template_version FROM portals WHERE id = ?", portal_id)
            if row is None or row["current_template_version"] is None:
                return None
            return await self._template(db, portal_id, row["current_template_version"])

    async def list_template_versions(self, portal_id: str) -> list[TemplateVersion]:
        """Newest first."""
        async with self._tx() as db:
            rows = await _all(
                db, "SELECT * FROM template_versions WHERE portal_id = ? ORDER BY version DESC", portal_id
            )
        return [_template(r) for r in rows]

    async def _template(self, db: aiosqlite.Connection, portal_id: str, version: int) -> TemplateVersion | None:
        row = await _one(db, "SELECT * FROM template_versions WHERE portal_id = ? AND version = ?", portal_id, version)
        return None if row is None else _template(row)

    # ------------------------------------------------------------------ tenders

    async def upsert_tenders(
        self, portal_id: str, tenders: Iterable[TenderUpsert], *, now: datetime | None = None
    ) -> UpsertSummary:
        """Insert or update tenders from a validated read.

        `last_changed_at` moves to `now` for new tenders and when the content hash or status differs. `last_seen_at`
        moves for every tender passed in. Call only with tenders from a read that passed validation.
        """
        ts = to_db(now or utcnow())
        summary = UpsertSummary(new=[], changed=[], unchanged=[])
        async with self._tx() as db:
            for t in tenders:
                row = await _one(
                    db,
                    "SELECT content_hash, status FROM tenders WHERE portal_id = ? AND tender_id = ?",
                    portal_id,
                    t.tender_id,
                )
                data = json.dumps(t.data)
                if row is None:
                    await db.execute(
                        "INSERT INTO tenders (portal_id, tender_id, data, content_hash, status, first_seen_at,"
                        " last_seen_at, last_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (portal_id, t.tender_id, data, t.content_hash, t.status, ts, ts, ts),
                    )
                    summary.new.append(t.tender_id)
                elif row["content_hash"] != t.content_hash or row["status"] != t.status:
                    await db.execute(
                        "UPDATE tenders SET data = ?, content_hash = ?, status = ?, last_seen_at = ?,"
                        " last_changed_at = ? WHERE portal_id = ? AND tender_id = ?",
                        (data, t.content_hash, t.status, ts, ts, portal_id, t.tender_id),
                    )
                    summary.changed.append(t.tender_id)
                else:
                    await db.execute(
                        "UPDATE tenders SET last_seen_at = ? WHERE portal_id = ? AND tender_id = ?",
                        (ts, portal_id, t.tender_id),
                    )
                    summary.unchanged.append(t.tender_id)
        return summary

    async def close_unlisted_tenders(
        self, portal_id: str, listed_ids: Sequence[str], *, now: datetime | None = None
    ) -> list[str]:
        """Set `open` tenders the portal no longer lists to `closed` (a status change, so `last_changed_at` moves)."""
        ts = to_db(now or utcnow())
        listed = set(listed_ids)
        async with self._tx() as db:
            rows = await _all(db, "SELECT tender_id FROM tenders WHERE portal_id = ? AND status = 'open'", portal_id)
            closed = [r["tender_id"] for r in rows if r["tender_id"] not in listed]
            await db.executemany(
                "UPDATE tenders SET status = 'closed', last_changed_at = ? WHERE portal_id = ? AND tender_id = ?",
                [(ts, portal_id, tid) for tid in closed],
            )
        return closed

    async def list_tenders(self, portal_id: str, *, changed_since: datetime | None = None) -> list[StoredTender]:
        """Tenders with their stored documents.

        `changed_since` keeps only those whose `last_changed_at` is strictly later.
        """
        sql = "SELECT * FROM tenders WHERE portal_id = ?"
        params: tuple = (portal_id,)
        if changed_since is not None:
            sql += " AND last_changed_at > ?"
            params += (to_db(changed_since),)
        sql += " ORDER BY first_seen_at, tender_id"
        async with self._tx() as db:
            rows = await _all(db, sql, *params)
            docs = await _all(db, "SELECT * FROM documents WHERE portal_id = ? ORDER BY created_at, id", portal_id)
        by_tender: dict[str, list[DocumentFile]] = {}
        for d in docs:
            by_tender.setdefault(d["tender_id"], []).append(_document(d))
        return [_tender(r, by_tender.get(r["tender_id"], [])) for r in rows]

    async def get_tender(self, portal_id: str, tender_id: str) -> StoredTender | None:
        async with self._tx() as db:
            row = await _one(db, "SELECT * FROM tenders WHERE portal_id = ? AND tender_id = ?", portal_id, tender_id)
            if row is None:
                return None
            docs = await _all(
                db,
                "SELECT * FROM documents WHERE portal_id = ? AND tender_id = ? ORDER BY created_at, id",
                portal_id,
                tender_id,
            )
        return _tender(row, [_document(d) for d in docs])

    # ------------------------------------------------------------------ documents

    async def add_document(
        self,
        portal_id: str,
        tender_id: str,
        *,
        source_url: str,
        file_name: str,
        size_bytes: int,
        sha256: str,
        archive_member: str = "",
    ) -> DocumentFile:
        """Record a file already written to `$DATA_DIR/files/<sha256>`. Re-adding the same source replaces the row."""
        async with self._tx() as db:
            await db.execute(
                "INSERT INTO documents (id, portal_id, tender_id, source_url, archive_member, file_name, size_bytes,"
                " sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (portal_id, tender_id, source_url, archive_member) DO UPDATE SET"
                " file_name = excluded.file_name, size_bytes = excluded.size_bytes, sha256 = excluded.sha256",
                (
                    uuid.uuid4().hex,
                    portal_id,
                    tender_id,
                    source_url,
                    archive_member,
                    file_name,
                    size_bytes,
                    sha256,
                    to_db(utcnow()),
                ),
            )
            row = await _one(
                db,
                "SELECT * FROM documents WHERE portal_id = ? AND tender_id = ? AND source_url = ?"
                " AND archive_member = ?",
                portal_id,
                tender_id,
                source_url,
                archive_member,
            )
        return _document(row)

    async def remove_documents(self, portal_id: str, tender_id: str, *, keep_source_urls: Iterable[str]) -> int:
        """Drop a tender's document rows except those from `keep_source_urls`.

        The files stay on disk: other tenders may share them.
        """
        keep = list(dict.fromkeys(keep_source_urls))
        sql = "DELETE FROM documents WHERE portal_id = ? AND tender_id = ?"
        params: tuple = (portal_id, tender_id)
        if keep:
            sql += f" AND source_url NOT IN ({','.join('?' * len(keep))})"
            params += tuple(keep)
        async with self._tx() as db:
            cur = await db.execute(sql, params)
            return cur.rowcount

    async def get_file(self, sha256: str) -> DocumentFile | None:
        """Look up a stored file by its id in `/files/{fileId}` (the sha256). Any row with that content will do."""
        async with self._tx() as db:
            row = await _one(db, "SELECT * FROM documents WHERE sha256 = ? ORDER BY created_at LIMIT 1", sha256)
        return None if row is None else _document(row)

    # ------------------------------------------------------------------ jobs

    async def enqueue_job(
        self,
        kind: JobKind,
        *,
        portal_id: str | None = None,
        payload: dict[str, Any] | None = None,
        run_after: datetime | None = None,
    ) -> Job:
        now = utcnow()
        async with self._tx() as db:
            cur = await db.execute(
                "INSERT INTO jobs (kind, portal_id, payload, status, run_after, created_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?)",
                (kind, portal_id, json.dumps(payload or {}), to_db(run_after or now), to_db(now)),
            )
            return await self._job(db, cur.lastrowid)

    async def claim_next_job(
        self, kinds: Sequence[JobKind] | None = None, *, now: datetime | None = None
    ) -> Job | None:
        """Atomically take the oldest due queued job and mark it running (attempts + 1)."""
        ts = to_db(now or utcnow())
        sql = "SELECT id FROM jobs WHERE status = 'queued' AND run_after <= ?"
        params: tuple = (ts,)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += tuple(kinds)
        sql += " ORDER BY run_after, id LIMIT 1"
        async with self._tx() as db:
            row = await _one(db, sql, *params)
            if row is None:
                return None
            await db.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                (ts, row["id"]),
            )
            return await self._job(db, row["id"])

    async def finish_job(self, job_id: int, *, ok: bool = True, error: str | None = None) -> Job:
        async with self._tx() as db:
            await db.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                ("done" if ok else "failed", to_db(utcnow()), error, job_id),
            )
            job = await self._job(db, job_id)
        if job is None:
            raise NotFound(f"job {job_id}")
        return job

    async def retry_job(self, job_id: int, *, run_after: datetime, error: str | None = None) -> Job:
        """Put a running job back in the queue for a later attempt."""
        async with self._tx() as db:
            await db.execute(
                "UPDATE jobs SET status = 'queued', run_after = ?, started_at = NULL, error = ? WHERE id = ?",
                (to_db(run_after), error, job_id),
            )
            job = await self._job(db, job_id)
        if job is None:
            raise NotFound(f"job {job_id}")
        return job

    async def set_job_run(self, job_id: int, run_id: str) -> None:
        async with self._tx() as db:
            await db.execute("UPDATE jobs SET run_id = ? WHERE id = ?", (run_id, job_id))

    async def requeue_running_jobs(self) -> int:
        """At startup: jobs left `running` by a previous process go back to `queued`. Returns how many."""
        async with self._tx() as db:
            cur = await db.execute("UPDATE jobs SET status = 'queued', started_at = NULL WHERE status = 'running'")
            return cur.rowcount

    async def active_jobs(self, portal_id: str, kinds: Sequence[JobKind] | None = None) -> list[Job]:
        """Queued or running jobs for a portal, oldest first (for the one-active-job-per-portal rule)."""
        sql = "SELECT * FROM jobs WHERE portal_id = ? AND status IN ('queued', 'running')"
        params: tuple = (portal_id,)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += tuple(kinds)
        async with self._tx() as db:
            rows = await _all(db, sql + " ORDER BY id", *params)
        return [_job(r) for r in rows]

    async def _job(self, db: aiosqlite.Connection, job_id: int | None) -> Job | None:
        row = await _one(db, "SELECT * FROM jobs WHERE id = ?", job_id)
        return None if row is None else _job(row)


# ---------------------------------------------------------------------- helpers


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


async def _one(db: aiosqlite.Connection, sql: str, *params: Any) -> sqlite3.Row | None:
    return await (await db.execute(sql, params)).fetchone()


async def _all(db: aiosqlite.Connection, sql: str, *params: Any) -> list[sqlite3.Row]:
    return list(await (await db.execute(sql, params)).fetchall())


def _portal(r: sqlite3.Row) -> Portal:
    return Portal(
        id=r["id"],
        url=r["url"],
        normalized_url=r["normalized_url"],
        state=r["state"],
        reason=r["reason"],
        tender_count=r["tender_count"],
        last_success_at=from_db(r["last_success_at"]),
        current_template_version=r["current_template_version"],
        created_at=from_db(r["created_at"]),
        updated_at=from_db(r["updated_at"]),
        deleted_at=from_db(r["deleted_at"]),
    )


def _run(r: sqlite3.Row) -> Run:
    return Run(
        id=r["id"],
        portal_id=r["portal_id"],
        kind=r["kind"],
        started_at=from_db(r["started_at"]),
        finished_at=from_db(r["finished_at"]),
        result=r["result"],
        template_version=r["template_version"],
        tender_count=r["tender_count"],
        details=json.loads(r["details"]),
        snapshot=None if r["snapshot"] is None else json.loads(r["snapshot"]),
    )


def _template(r: sqlite3.Row) -> TemplateVersion:
    return TemplateVersion(
        portal_id=r["portal_id"],
        version=r["version"],
        template=json.loads(r["template"]),
        notes=r["notes"],
        created_at=from_db(r["created_at"]),
        promoted_at=from_db(r["promoted_at"]),
    )


def _tender(r: sqlite3.Row, documents: list[DocumentFile]) -> StoredTender:
    return StoredTender(
        portal_id=r["portal_id"],
        tender_id=r["tender_id"],
        data=json.loads(r["data"]),
        content_hash=r["content_hash"],
        status=r["status"],
        first_seen_at=from_db(r["first_seen_at"]),
        last_seen_at=from_db(r["last_seen_at"]),
        last_changed_at=from_db(r["last_changed_at"]),
        documents=documents,
    )


def _document(r: sqlite3.Row) -> DocumentFile:
    return DocumentFile(
        id=r["id"],
        portal_id=r["portal_id"],
        tender_id=r["tender_id"],
        source_url=r["source_url"],
        archive_member=r["archive_member"],
        file_name=r["file_name"],
        size_bytes=r["size_bytes"],
        sha256=r["sha256"],
        created_at=from_db(r["created_at"]),
    )


def _job(r: sqlite3.Row) -> Job:
    return Job(
        id=r["id"],
        kind=r["kind"],
        portal_id=r["portal_id"],
        payload=json.loads(r["payload"]),
        status=r["status"],
        attempts=r["attempts"],
        run_after=from_db(r["run_after"]),
        created_at=from_db(r["created_at"]),
        started_at=from_db(r["started_at"]),
        finished_at=from_db(r["finished_at"]),
        error=r["error"],
        run_id=r["run_id"],
    )

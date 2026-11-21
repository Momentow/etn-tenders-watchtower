"""Schema migrations, applied in order at startup. Append new entries; never edit a shipped one.

`PRAGMA user_version` records how many have been applied.
"""

MIGRATIONS: list[str] = [
    # 1 — initial schema (T-004)
    """
    CREATE TABLE portals (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        normalized_url TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (state IN ('integrating', 'serving', 'healing', 'failed')),
        reason TEXT NOT NULL,
        last_success_at TEXT,
        current_template_version INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    );

    CREATE TABLE template_versions (
        portal_id TEXT NOT NULL REFERENCES portals(id),
        version INTEGER NOT NULL,
        template TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        promoted_at TEXT,
        PRIMARY KEY (portal_id, version)
    );

    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        portal_id TEXT NOT NULL REFERENCES portals(id),
        kind TEXT NOT NULL CHECK (kind IN ('read', 'repair')),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        result TEXT CHECK (result IN ('ok', 'failed')),
        template_version INTEGER,
        tender_count INTEGER,
        details TEXT NOT NULL DEFAULT '{}',
        snapshot TEXT
    );
    CREATE INDEX runs_portal_started ON runs (portal_id, started_at DESC);

    CREATE TABLE tenders (
        portal_id TEXT NOT NULL REFERENCES portals(id),
        tender_id TEXT NOT NULL,
        data TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'withdrawn')),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        last_changed_at TEXT NOT NULL,
        PRIMARY KEY (portal_id, tender_id)
    );
    CREATE INDEX tenders_portal_changed ON tenders (portal_id, last_changed_at);

    CREATE TABLE documents (
        id TEXT PRIMARY KEY,
        portal_id TEXT NOT NULL,
        tender_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        archive_member TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (portal_id, tender_id, source_url, archive_member),
        FOREIGN KEY (portal_id, tender_id) REFERENCES tenders(portal_id, tender_id)
    );
    CREATE INDEX documents_sha256 ON documents (sha256);

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ('attach', 'read', 'investigate', 'download')),
        portal_id TEXT REFERENCES portals(id),
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0,
        run_after TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        error TEXT,
        run_id TEXT
    );
    CREATE INDEX jobs_ready ON jobs (status, run_after, id);
    CREATE INDEX jobs_portal_status ON jobs (portal_id, status);
    """,
    # 2 — model usage per run, scenario and tier (T-099, ADR 0017)
    """
    CREATE TABLE model_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT REFERENCES runs(id),
        portal_id TEXT NOT NULL REFERENCES portals(id),
        scenario TEXT NOT NULL,
        tier TEXT NOT NULL,
        model TEXT NOT NULL,
        requests INTEGER NOT NULL,
        input_tokens INTEGER NOT NULL,
        cached_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_usd REAL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX model_usage_portal ON model_usage (portal_id, scenario);
    """,
]

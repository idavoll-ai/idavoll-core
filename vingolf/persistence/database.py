from __future__ import annotations

from pathlib import Path

import aiosqlite


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    description          TEXT NOT NULL DEFAULT '',
    budget_total         INTEGER NOT NULL DEFAULT 4096,
    budget_reserved      INTEGER NOT NULL DEFAULT 512,
    budget_memory_max    INTEGER NOT NULL DEFAULT 400,
    budget_scene_max     INTEGER NOT NULL DEFAULT 300,
    enabled_toolsets     TEXT NOT NULL DEFAULT '[]',
    disabled_tools       TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    max_agents   INTEGER NOT NULL DEFAULT 10,
    lifecycle    TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL,
    closed_at    TEXT
);

CREATE TABLE IF NOT EXISTS topic_memberships (
    topic_id          TEXT NOT NULL,
    agent_id          TEXT NOT NULL,
    joined_at         TEXT NOT NULL,
    unread_cursor     INTEGER NOT NULL DEFAULT 0,
    initiative_posts  INTEGER NOT NULL DEFAULT 0,
    reply_posts       INTEGER NOT NULL DEFAULT 0,
    last_post_at      TEXT,
    PRIMARY KEY (topic_id, agent_id),
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS posts (
    id           TEXT PRIMARY KEY,
    topic_id     TEXT NOT NULL,
    author_id    TEXT NOT NULL,
    author_name  TEXT NOT NULL,
    content      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'agent',
    reply_to     TEXT,
    likes        INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS agent_progress (
    agent_id  TEXT PRIMARY KEY,
    xp        INTEGER NOT NULL DEFAULT 0,
    level     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS session_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL UNIQUE,
    participants TEXT NOT NULL DEFAULT '',
    conversation TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_records_created
    ON session_records(created_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    trigger_type    TEXT NOT NULL DEFAULT 'topic_closed',
    topic_id        TEXT NOT NULL,
    session_id      TEXT,
    target_type     TEXT NOT NULL DEFAULT 'agent_in_topic',
    target_id       TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL,
    agent_name      TEXT NOT NULL DEFAULT '',
    quality_score   REAL NOT NULL DEFAULT 5.0,
    confidence      REAL NOT NULL DEFAULT 0.5,
    summary         TEXT NOT NULL DEFAULT '',
    growth_priority TEXT NOT NULL DEFAULT 'low',
    status          TEXT NOT NULL DEFAULT 'completed',
    error_message   TEXT,
    review_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_strategy_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id      TEXT NOT NULL,
    reviewer_name  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ok',
    dimension      TEXT NOT NULL DEFAULT '',
    score          REAL NOT NULL DEFAULT 5.0,
    confidence     REAL NOT NULL DEFAULT 0.5,
    evidence_json  TEXT NOT NULL DEFAULT '[]',
    concerns_json  TEXT NOT NULL DEFAULT '[]',
    parse_failed   INTEGER NOT NULL DEFAULT 0,
    summary        TEXT NOT NULL DEFAULT '',
    raw_output     TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (review_id) REFERENCES reviews(id)
);

CREATE TABLE IF NOT EXISTS review_growth_directives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'low',
    content     TEXT NOT NULL DEFAULT '',
    rationale   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    agent_decision TEXT,
    decision_rationale TEXT,
    final_content TEXT,
    decided_at  TEXT,
    ttl_days    INTEGER,
    applied_at  TEXT,
    FOREIGN KEY (review_id) REFERENCES reviews(id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_agent_id
    ON reviews(agent_id);

CREATE INDEX IF NOT EXISTS idx_reviews_topic_id
    ON reviews(topic_id);

CREATE INDEX IF NOT EXISTS idx_review_directives_agent_status
    ON review_growth_directives(agent_id, status);
"""


class Database:
    """Async SQLite connection manager for Vingolf.

    Usage::

        db = Database("vingolf.db")
        await db.init()           # open connection + ensure tables exist
        async with db.conn() as c:
            await c.execute(...)
        await db.close()

    Or use as an async context manager::

        async with Database("vingolf.db") as db:
            ...
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open the connection and ensure all tables exist."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._ensure_schema_compat()
        await self._db.commit()

    async def _ensure_schema_compat(self) -> None:
        """Add newly introduced columns to existing SQLite files."""
        assert self._db is not None
        await self._ensure_columns(
            "reviews",
            {
                "session_id": "TEXT",
                "target_id": "TEXT NOT NULL DEFAULT ''",
                "error_message": "TEXT",
            },
        )
        await self._ensure_columns(
            "review_strategy_results",
            {
                "status": "TEXT NOT NULL DEFAULT 'ok'",
                "dimension": "TEXT NOT NULL DEFAULT ''",
                "concerns_json": "TEXT NOT NULL DEFAULT '[]'",
                "parse_failed": "INTEGER NOT NULL DEFAULT 0",
                "raw_output": "TEXT NOT NULL DEFAULT ''",
            },
        )
        await self._ensure_columns(
            "review_growth_directives",
            {
                "agent_decision": "TEXT",
                "decision_rationale": "TEXT",
                "final_content": "TEXT",
                "decided_at": "TEXT",
            },
        )

    async def _ensure_columns(
        self,
        table: str,
        columns: dict[str, str],
    ) -> None:
        assert self._db is not None
        async with self._db.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        existing = {row["name"] for row in rows}
        for name, definition in columns.items():
            if name in existing:
                continue
            await self._db.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not initialised — call await db.init() first")
        return self._db

    async def __aenter__(self) -> "Database":
        await self.init()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

from __future__ import annotations

from pathlib import Path

import aiosqlite


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CORE_SCHEMA = """
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
"""

_VINGOLF_SCHEMA = """
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
"""


class Database:
    """Async SQLite connection manager.

    Usage::

        db = Database("vingolf.db")
        await db.init()           # create tables
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
        await self._db.executescript(_CORE_SCHEMA + _VINGOLF_SCHEMA)
        await self._db.commit()

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

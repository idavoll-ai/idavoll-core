from __future__ import annotations

from typing import TYPE_CHECKING

from ..progress import AgentProgress

if TYPE_CHECKING:
    from .database import Database


class AgentProgressRepository:
    """Async CRUD for AgentProgress (XP / Level) rows."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def save(self, progress: AgentProgress) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO agent_progress (agent_id, xp, level)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                xp    = excluded.xp,
                level = excluded.level
            """,
            (progress.agent_id, progress.xp, progress.level),
        )
        await self._db.conn.commit()

    async def get(self, agent_id: str) -> AgentProgress | None:
        async with self._db.conn.execute(
            "SELECT * FROM agent_progress WHERE agent_id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return AgentProgress(agent_id=row["agent_id"], xp=row["xp"], level=row["level"])

    async def get_or_create(self, agent_id: str) -> AgentProgress:
        progress = await self.get(agent_id)
        if progress is None:
            progress = AgentProgress(agent_id=agent_id)
            await self.save(progress)
        return progress

    async def all(self) -> list[AgentProgress]:
        async with self._db.conn.execute("SELECT * FROM agent_progress") as cur:
            rows = await cur.fetchall()
        return [AgentProgress(agent_id=r["agent_id"], xp=r["xp"], level=r["level"]) for r in rows]

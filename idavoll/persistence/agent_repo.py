from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..agent.profile import AgentProfile, ContextBudget

if TYPE_CHECKING:
    from .database import Database


class AgentProfileRepository:
    """Async CRUD for AgentProfile rows in SQLite."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def save(self, profile: AgentProfile) -> None:
        """Insert or replace an AgentProfile."""
        await self._db.conn.execute(
            """
            INSERT INTO agents
                (id, name, description,
                 budget_total, budget_reserved, budget_memory_max, budget_scene_max,
                 enabled_toolsets, disabled_tools)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name                = excluded.name,
                description         = excluded.description,
                budget_total        = excluded.budget_total,
                budget_reserved     = excluded.budget_reserved,
                budget_memory_max   = excluded.budget_memory_max,
                budget_scene_max    = excluded.budget_scene_max,
                enabled_toolsets    = excluded.enabled_toolsets,
                disabled_tools      = excluded.disabled_tools
            """,
            (
                profile.id,
                profile.name,
                profile.description,
                profile.budget.total,
                profile.budget.reserved_for_output,
                profile.budget.memory_context_max,
                profile.budget.scene_context_max,
                json.dumps(profile.enabled_toolsets),
                json.dumps(profile.disabled_tools),
            ),
        )
        await self._db.conn.commit()

    async def get(self, agent_id: str) -> AgentProfile | None:
        """Return an AgentProfile by id, or None if not found."""
        async with self._db.conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return self._row_to_profile(row)

    async def all(self) -> list[AgentProfile]:
        """Return all stored AgentProfiles."""
        async with self._db.conn.execute("SELECT * FROM agents ORDER BY created_at") as cur:
            rows = await cur.fetchall()
        return [self._row_to_profile(r) for r in rows]

    async def delete(self, agent_id: str) -> None:
        await self._db.conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await self._db.conn.commit()

    @staticmethod
    def _row_to_profile(row) -> AgentProfile:
        return AgentProfile(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            budget=ContextBudget(
                total=row["budget_total"],
                reserved_for_output=row["budget_reserved"],
                memory_context_max=row["budget_memory_max"],
                scene_context_max=row["budget_scene_max"],
            ),
            enabled_toolsets=json.loads(row["enabled_toolsets"]),
            disabled_tools=json.loads(row["disabled_tools"]),
        )

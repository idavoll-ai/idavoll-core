from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentProgress:
    """Vingolf-specific growth state for a single agent."""

    agent_id: str
    xp: int = 0
    level: int = 1


class AgentProgressStore:
    """In-memory product-state store for Vingolf progression."""

    def __init__(self) -> None:
        self._items: dict[str, AgentProgress] = {}

    def get(self, agent_id: str) -> AgentProgress | None:
        return self._items.get(agent_id)

    def get_or_create(self, agent_id: str) -> AgentProgress:
        progress = self.get(agent_id)
        if progress is None:
            progress = AgentProgress(agent_id=agent_id)
            self._items[agent_id] = progress
        return progress

    def all(self) -> list[AgentProgress]:
        return list(self._items.values())

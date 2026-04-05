from __future__ import annotations

from typing import Callable

from .memory import AgentMemory
from .profile import AgentProfile


class Agent:
    """Runtime representation of an agent — profile + memory + mutable state."""

    def __init__(self, profile: AgentProfile, memory: AgentMemory | None = None) -> None:
        self.profile = profile
        self.id = profile.id
        self.memory: AgentMemory = memory or AgentMemory()
        # Growth state — managed by Vingolf's GrowthPlugin, stored here for convenience
        self.xp: int = 0
        self.level: int = 1

    def __repr__(self) -> str:
        return f"Agent(id={self.id!r}, name={self.profile.name!r}, level={self.level})"


class AgentRegistry:
    """In-memory store for all agents. Persistence is delegated to the application layer."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, profile: AgentProfile, memory: AgentMemory | None = None) -> Agent:
        agent = Agent(profile, memory)
        self._agents[agent.id] = agent
        return agent

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_or_raise(self, agent_id: str) -> Agent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id!r} not found in registry")
        return agent

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def update(self, agent_id: str, updater: Callable[[Agent], None]) -> Agent:
        """Apply an in-place mutation to an agent. Used by GrowthPlugin."""
        agent = self.get_or_raise(agent_id)
        updater(agent)
        return agent

    def remove(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .profile import AgentProfile

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager
    from ..session.search import SessionSearch
    from ..skills.library import SkillsLibrary
    from ..tools.registry import ToolSpec
    from .workspace import ProfileWorkspace


@dataclass(slots=True)
class Agent:
    """Runtime agent state tracked by the framework and product plugins."""

    profile: AgentProfile
    metadata: dict[str, Any] = field(default_factory=dict)
    workspace: "ProfileWorkspace | None" = field(default=None, compare=False)
    memory: "MemoryManager | None" = field(default=None, compare=False)
    skills: "SkillsLibrary | None" = field(default=None, compare=False)
    session_search: "SessionSearch | None" = field(default=None, compare=False)
    tools: "list[ToolSpec]" = field(default_factory=list, compare=False)

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def name(self) -> str:
        return self.profile.name


class AgentRegistry:
    """In-memory control-plane metadata store for agents."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, profile: AgentProfile) -> Agent:
        agent = Agent(profile=profile)
        self._agents[agent.id] = agent
        return agent

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_or_raise(self, agent_id: str) -> Agent:
        agent = self.get(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id!r} not found")
        return agent

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def update(self, agent_id: str, updater: Callable[[Agent], None]) -> Agent:
        agent = self.get_or_raise(agent_id)
        updater(agent)
        return agent

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

from .profile import AgentProfile

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager
    from ..session.search import SessionSearch
    from ..skills.library import SkillsLibrary
    from ..tools.registry import ToolSpec, ToolsetManager
    from .workspace import ProfileWorkspace


class AgentLoader(Protocol):
    """Loader protocol for restoring an AgentProfile from external storage.

    Implement this in the product layer (e.g. Vingolf) and register it via
    ``IdavollApp.set_agent_loader()``.  Return *None* when the agent is not
    found in the backing store.
    """

    async def __call__(self, agent_id: str) -> AgentProfile | None:
        ...


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

    def __init__(self, toolsets: "ToolsetManager | None" = None) -> None:
        self._agents: dict[str, Agent] = {}
        self._toolsets = toolsets

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

    def delete(self, agent_id: str) -> Agent | None:
        """Remove an agent from the in-memory registry."""
        return self._agents.pop(agent_id, None)

    def update(self, agent_id: str, updater: Callable[[Agent], None]) -> Agent:
        agent = self.get_or_raise(agent_id)
        updater(agent)
        return agent

    def unlock_toolset(self, agent_id: str, toolset_name: str) -> Agent:
        """Add *toolset_name* to the agent's ``enabled_toolsets`` and re-resolve tools.

        Idempotent: calling this twice with the same toolset is a no-op.
        If no ``ToolsetManager`` was provided at construction time, only the
        profile is updated — callers are responsible for re-resolving tools.
        """
        agent = self.get_or_raise(agent_id)
        if toolset_name not in agent.profile.enabled_toolsets:
            agent.profile.enabled_toolsets.append(toolset_name)
        if self._toolsets is not None:
            agent.tools = self._toolsets.resolve(
                agent.profile.enabled_toolsets,
                disabled_tools=agent.profile.disabled_tools,
            )
        return agent

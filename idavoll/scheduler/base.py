from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..session.session import Session


class SchedulerStrategy(ABC):
    """
    Decides which agent speaks next and whether the session should continue.

    Implement this interface to inject custom scheduling logic into Idavoll.
    Vingolf's RelevanceStrategy (selects agents by topic relevance) is one example.
    """

    @abstractmethod
    def select_next(self, session: "Session", agents: list["Agent"]) -> "Agent":
        """Return the agent that should produce the next message."""
        ...

    @abstractmethod
    def should_continue(self, session: "Session") -> bool:
        """Return False to stop the scheduling loop (e.g. quota exhausted, session closed)."""
        ...

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .base import SchedulerStrategy
from ..session.session import SessionState

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..session.session import Session


class RoundRobinStrategy(SchedulerStrategy):
    """Cycles through participants in order. Default strategy."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def select_next(self, session: "Session", agents: list["Agent"]) -> "Agent":
        count = self._counters.get(session.id, 0)
        agent = agents[count % len(agents)]
        self._counters[session.id] = count + 1
        return agent

    def should_continue(self, session: "Session") -> bool:
        return session.state != SessionState.CLOSED


class RandomStrategy(SchedulerStrategy):
    """Picks a random participant each turn."""

    def select_next(self, session: "Session", agents: list["Agent"]) -> "Agent":
        return random.choice(agents)

    def should_continue(self, session: "Session") -> bool:
        return session.state != SessionState.CLOSED

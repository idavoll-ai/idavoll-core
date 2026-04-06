from __future__ import annotations

import random
from typing import TYPE_CHECKING

from idavoll.scheduler.base import SchedulerStrategy
from idavoll.session.session import SessionState

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.session.session import Session

    from .models import Topic


class TopicRelevanceStrategy(SchedulerStrategy):
    """
    Selects the next agent by scoring the overlap between the topic's tags and
    each agent's knowledge domains.

    A small random jitter is added so agents with identical scores don't always
    speak in the same order — this keeps the discussion from feeling mechanical.

    Falls back to random selection when the session has no topic metadata.
    """

    def select_next(self, session: "Session", agents: list["Agent"]) -> "Agent":
        topic: Topic | None = session.metadata.get("topic")

        # Determine the agent who spoke last (to penalise back-to-back selection)
        last_agent_id: str | None = None
        if session.messages:
            last_agent_id = session.messages[-1].agent_id

        # With only one participant there is nothing to alternate
        if len(agents) == 1:
            return agents[0]

        if topic is None or not topic.tags:
            candidates = [a for a in agents if a.id != last_agent_id] or agents
            return random.choice(candidates)

        topic_tags = {t.lower() for t in topic.tags}

        def score(agent: "Agent") -> float:
            # Match topic tags against the agent's identity text (role + goal)
            identity = agent.profile.identity
            identity_text = f"{identity.role} {identity.goal}".lower()
            overlap = sum(1 for tag in topic_tags if tag.lower() in identity_text)
            # Penalise the agent who just spoke so they are not selected again
            # unless they are the only viable candidate
            penalty = 10.0 if agent.id == last_agent_id else 0.0
            return overlap + random.random() * 0.5 - penalty  # jitter in [0, 0.5)

        return max(agents, key=score)

    def should_continue(self, session: "Session") -> bool:
        return session.state != SessionState.CLOSED

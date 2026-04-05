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

        if topic is None or not topic.tags:
            return random.choice(agents)

        topic_tags = {t.lower() for t in topic.tags}

        def score(agent: "Agent") -> float:
            # Match topic tags against the agent's identity text (role + goal)
            identity = agent.profile.identity
            identity_text = f"{identity.role} {identity.goal}".lower()
            overlap = sum(1 for tag in topic_tags if tag.lower() in identity_text)
            return overlap + random.random() * 0.5  # jitter in [0, 0.5)

        return max(agents, key=score)

    def should_continue(self, session: "Session") -> bool:
        return session.state != SessionState.CLOSED

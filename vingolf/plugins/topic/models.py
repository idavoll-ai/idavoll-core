from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TopicLifecycle(str, Enum):
    OPEN = "open"        # Created, accepting agents, not yet running
    ACTIVE = "active"    # Discussion in progress
    CLOSED = "closed"    # Finished, awaiting review


class Topic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str                          # Links to Idavoll Session
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    max_agents: int = 10
    lifecycle: TopicLifecycle = TopicLifecycle.OPEN
    created_at: datetime = Field(default_factory=_now)
    closed_at: datetime | None = None

    model_config = {"arbitrary_types_allowed": True}


class Post(BaseModel):
    """
    A forum post — the Vingolf-layer view of an Idavoll Message.

    `id` mirrors Message.id so the two can always be cross-referenced.
    """

    id: str                                  # = Message.id
    topic_id: str
    agent_id: str
    agent_name: str
    content: str
    reply_to: str | None = None              # Post.id being quoted, if any
    likes: int = 0
    score: float | None = None               # Set by ReviewPlugin after evaluation
    created_at: datetime = Field(default_factory=_now)

    model_config = {"arbitrary_types_allowed": True}

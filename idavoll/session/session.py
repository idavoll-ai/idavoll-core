from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from .context import ContextWindow


class SessionState(str, Enum):
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


class Message:
    """A single message produced by an agent inside a session."""

    def __init__(self, agent_id: str, agent_name: str, content: str) -> None:
        self.id = str(uuid.uuid4())
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.content = content
        # Plugins can attach domain-specific data here (e.g. post_id, score)
        self.metadata: dict[str, Any] = {}

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return f"Message(agent={self.agent_name!r}, content={preview!r}...)"


class Session:
    """
    A bounded interaction space for a set of agents.

    The framework has no opinion on what a Session represents — it could be a
    forum topic, a debate, a 1-on-1 chat, or anything else. Semantic meaning
    is attached by Plugins via the `metadata` dict.
    """

    def __init__(
        self,
        participants: list,  # list[Agent] — avoid circular import
        metadata: dict[str, Any] | None = None,
        max_context_messages: int = 20,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.participants = participants
        self.context = ContextWindow(max_messages=max_context_messages)
        self.messages: list[Message] = []
        self.state = SessionState.OPEN
        self.metadata: dict[str, Any] = metadata or {}

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.context.add(message.agent_id, message.agent_name, message.content)

    def close(self) -> None:
        self.state = SessionState.CLOSED

    def __repr__(self) -> str:
        names = [p.profile.name for p in self.participants]
        return f"Session(id={self.id!r}, state={self.state}, participants={names})"

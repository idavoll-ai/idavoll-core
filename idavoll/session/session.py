from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from .context import SessionServices


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(str, Enum):
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(slots=True)
class Message:
    """A single message stored inside a session history."""

    agent_id: str
    agent_name: str
    content: str
    role: Literal["user", "assistant"] = "assistant"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)


class Session:
    """Generic interaction space shared by the core runtime and product modules."""

    def __init__(
        self,
        participants: list[Any],
        metadata: dict[str, Any] | None = None,
        max_context_messages: int = 20,
        services: SessionServices | None = None,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.participants = list(participants)
        self.metadata: dict[str, Any] = metadata or {}
        self.max_context_messages = max_context_messages
        self.messages: list[Message] = []
        self.state = SessionState.OPEN
        self.services = services or SessionServices()
        # Frozen system prompts keyed by agent_id.
        # Populated lazily on the first turn for each agent and never
        # recompiled within the same session (§9.2 Frozen Snapshot 原则).
        self.frozen_prompts: dict[str, str] = {}

    def add_participant(self, agent: Any) -> None:
        if not any(existing.id == agent.id for existing in self.participants):
            self.participants.append(agent)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def recent_messages(self) -> list[Message]:
        return self.messages[-self.max_context_messages :]

    def close(self) -> None:
        self.state = SessionState.CLOSED

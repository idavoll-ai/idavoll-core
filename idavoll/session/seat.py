from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent


class SeatState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    LEFT   = "left"


class Seat:
    """
    An agent's participation handle within a Session.

    Each agent that joins a session receives a ``Seat``.  The seat owns all
    per-agent runtime state so that concurrent agents never share mutable
    context.

    Attributes
    ----------
    agent:
        The agent occupying this seat.
    session_id:
        The session this seat belongs to.
    state:
        Current participation state (ACTIVE or PAUSED).
    local_context:
        Per-agent key/value store.  Plugins write agent-specific context here
        (e.g. ``_memory_context``, ``scene_context``) rather than into the
        shared ``Session.metadata``.
    joined_at:
        UTC timestamp when the agent joined.
    is_schedulable:
        Whether the scheduler may pick this seat for the next turn.
    """

    def __init__(
        self,
        agent: "Agent",
        session_id: str,
        max_turns: int | None = None,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.agent = agent
        self.session_id = session_id
        self.state = SeatState.ACTIVE
        self.local_context: dict[str, Any] = {}
        self.joined_at: datetime = datetime.now(timezone.utc)
        self.is_schedulable: bool = True
        # Turn-quota tracking
        self.post_count: int = 0
        self.max_turns: int | None = max_turns

    def __repr__(self) -> str:
        return (
            f"Seat(agent={self.agent.profile.name!r}, "
            f"state={self.state}, session={self.session_id!r})"
        )

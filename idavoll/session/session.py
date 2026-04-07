from __future__ import annotations

import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any

from .context import ContextWindow
from .seat import Seat, SeatState

if TYPE_CHECKING:
    from ..scheduler.base import SchedulerStrategy


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
        scheduler: "SchedulerStrategy | None" = None,
        per_agent_max_turns: int | None = None,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.participants = participants
        self.context = ContextWindow(max_messages=max_context_messages)
        self.messages: list[Message] = []
        self.state = SessionState.OPEN
        self.metadata: dict[str, Any] = metadata or {}
        # Per-session scheduler. When set, takes precedence over the app-level
        # default. Allows different sessions to use different strategies.
        self.scheduler: "SchedulerStrategy | None" = scheduler
        # When set, each agent may speak at most this many times in the session.
        self.per_agent_max_turns: int | None = per_agent_max_turns
        # Per-agent participation handles. Keyed by agent_id.
        # Each Seat owns the agent's local_context so that concurrent agents
        # never share mutable per-turn state.
        self.seats: dict[str, Seat] = {}
        # Initialise seats for agents provided at construction time.
        for agent in participants:
            self.seats[agent.id] = Seat(
                agent=agent,
                session_id=self.id,
                max_turns=per_agent_max_turns,
            )

    def seat_local(self, agent_id: str) -> dict[str, Any]:
        """Return the per-agent local-context dict, creating a bare Seat if needed.

        Prefer :meth:`add_participant` to register agents properly.  This
        fallback exists so that legacy plugin code using ``seat_local()``
        directly still works even if the agent was never added through the
        normal path.
        """
        if agent_id not in self.seats:
            bare: Seat = object.__new__(Seat)
            bare.local_context = {}
            self.seats[agent_id] = bare
        return self.seats[agent_id].local_context

    def add_participant(self, agent) -> None:  # agent: Agent
        """Add an agent to the session.

        Unlike the old implementation, this method no longer restricts joining
        to the OPEN state.  The session can accept new participants at any
        lifecycle stage — OPEN, ACTIVE, or even CLOSED (though scheduling a
        closed session is a no-op).

        Calling add_participant is idempotent: adding the same agent twice is
        a no-op.  If the agent previously left (seat state LEFT), their seat is
        re-activated and they are added back to the participant list.
        """
        existing_seat = self.seats.get(agent.id)
        if existing_seat is not None:
            if existing_seat.state == SeatState.LEFT:
                # Re-join: re-activate the existing seat
                existing_seat.state = SeatState.ACTIVE
                existing_seat.is_schedulable = True
                if not any(p.id == agent.id for p in self.participants):
                    self.participants.append(agent)
            # Already active / paused — idempotent, do nothing
            return
        self.participants.append(agent)
        self.seats[agent.id] = Seat(
            agent=agent,
            session_id=self.id,
            max_turns=self.per_agent_max_turns,
        )

    def remove_participant(self, agent) -> None:  # agent: Agent
        """Permanently remove an agent from the session.

        The agent's seat is kept for history but its state is set to LEFT and
        is_schedulable is cleared.  The agent is removed from the active
        participants list so the scheduler will never pick them again.
        """
        self.participants[:] = [p for p in self.participants if p.id != agent.id]
        if agent.id in self.seats:
            self.seats[agent.id].state = SeatState.LEFT
            self.seats[agent.id].is_schedulable = False

    def pause_participant(self, agent) -> None:  # agent: Agent
        """Temporarily pause an agent — they stay registered but won't be scheduled.

        Call :meth:`resume_participant` to bring them back.
        """
        self.participants[:] = [p for p in self.participants if p.id != agent.id]
        if agent.id in self.seats:
            self.seats[agent.id].state = SeatState.PAUSED
            self.seats[agent.id].is_schedulable = False

    def resume_participant(self, agent) -> None:  # agent: Agent
        """Resume a previously paused agent.

        If the agent has no seat (never joined), this is equivalent to
        :meth:`add_participant`.
        """
        seat = self.seats.get(agent.id)
        if seat is None or seat.state == SeatState.LEFT:
            self.add_participant(agent)
            return
        seat.state = SeatState.ACTIVE
        seat.is_schedulable = True
        if not any(p.id == agent.id for p in self.participants):
            self.participants.append(agent)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.context.add(message.agent_id, message.agent_name, message.content)

    def close(self) -> None:
        self.state = SessionState.CLOSED

    def __repr__(self) -> str:
        names = [p.profile.name for p in self.participants]
        return f"Session(id={self.id!r}, state={self.state}, participants={names})"

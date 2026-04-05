"""ObservabilityPlugin — structured logging and in-memory metrics."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ..agent.registry import Agent
from ..plugin.base import IdavollPlugin
from ..session.session import Message, Session
from .metrics import MetricsCollector

if TYPE_CHECKING:
    from ..app import IdavollApp

logger = logging.getLogger("idavoll")


class ObservabilityPlugin(IdavollPlugin):
    """
    Attaches structured logging and in-memory metrics to an IdavollApp.

    Install it like any other plugin::

        from idavoll.observability import ObservabilityPlugin, configure_logging

        configure_logging()          # set up JSON output to stderr
        obs = ObservabilityPlugin()
        app.use(obs)

        # ... run sessions ...

        print(obs.metrics.snapshot())

    Hooks subscribed:
        - ``session.created`` / ``session.closed``
        - ``session.message.after``
        - ``llm.generate.after``   (emitted by IdavollApp.run_session)
        - ``agent.created``
        - ``scheduler.selected``

    All events are logged to the ``idavoll`` logger at the configured
    *log_level*.  Metrics are accumulated in :attr:`metrics`.
    """

    name = "idavoll.observability"

    def __init__(self, log_level: int = logging.INFO) -> None:
        self._log_level = log_level
        self.metrics = MetricsCollector()
        self._session_start_times: dict[str, float] = {}

    def install(self, app: "IdavollApp") -> None:
        app.hooks.on("agent.created", self._on_agent_created)
        app.hooks.on("session.created", self._on_session_created)
        app.hooks.on("session.closed", self._on_session_closed)
        app.hooks.on("session.message.after", self._on_message_after)
        app.hooks.on("llm.generate.after", self._on_llm_generate_after)
        app.hooks.on("scheduler.selected", self._on_scheduler_selected)

    # ── Event handlers ──────────────────────────────────────────────────────

    def _on_agent_created(self, agent: Agent, **_: Any) -> None:
        self.metrics.increment("agents.created")
        logger.log(
            self._log_level,
            "agent.created",
            extra={
                "event": "agent.created",
                "agent_id": agent.id,
                "agent_name": agent.profile.name,
            },
        )

    def _on_session_created(self, session: Session, **_: Any) -> None:
        self._session_start_times[session.id] = time.monotonic()
        self.metrics.increment("sessions.total")
        logger.log(
            self._log_level,
            "session.created",
            extra={
                "event": "session.created",
                "session_id": session.id,
                "participants": [a.profile.name for a in session.participants],
                "participant_count": len(session.participants),
            },
        )

    def _on_session_closed(self, session: Session, **_: Any) -> None:
        start = self._session_start_times.pop(session.id, None)
        duration = round(time.monotonic() - start, 3) if start is not None else None
        message_count = len(session.messages)

        self.metrics.increment("sessions.closed")
        self.metrics.increment("messages.total", by=message_count)
        if duration is not None:
            self.metrics.record("session.duration_s", duration)

        logger.log(
            self._log_level,
            "session.closed",
            extra={
                "event": "session.closed",
                "session_id": session.id,
                "message_count": message_count,
                "duration_s": duration,
            },
        )

    def _on_message_after(self, session: Session, message: Message, **_: Any) -> None:
        content_len = len(message.content)
        self.metrics.increment("messages.total_chars", by=content_len)
        logger.log(
            self._log_level,
            "session.message.after",
            extra={
                "event": "session.message.after",
                "session_id": session.id,
                "agent_id": message.agent_id,
                "agent_name": message.agent_name,
                "content_length": content_len,
            },
        )

    def _on_llm_generate_after(
        self,
        agent: Agent,
        session: Session,
        latency_ms: float,
        content_length: int,
        **_: Any,
    ) -> None:
        self.metrics.increment("llm.calls")
        self.metrics.record("llm.latency_ms", latency_ms)
        self.metrics.increment(f"llm.calls_by_agent.{agent.profile.name}")
        logger.log(
            self._log_level,
            "llm.generate.after",
            extra={
                "event": "llm.generate.after",
                "session_id": session.id,
                "agent_id": agent.id,
                "agent_name": agent.profile.name,
                "latency_ms": round(latency_ms, 1),
                "content_length": content_length,
            },
        )

    def _on_scheduler_selected(self, session: Session, agent: Agent, **_: Any) -> None:
        self.metrics.increment(f"scheduler.selections.{agent.profile.name}")

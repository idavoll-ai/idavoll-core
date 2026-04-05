"""LangSmith integration plugin for Idavoll."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..agent.registry import Agent
from ..plugin.base import IdavollPlugin
from ..session.session import Session

if TYPE_CHECKING:
    from ..app import IdavollApp


class LangSmithPlugin(IdavollPlugin):
    """
    Wraps each Idavoll session as a LangSmith ``RunTree``, so all LLM calls
    inside a session appear as child spans in LangSmith's trace view.

    Requirements::

        pip install langsmith

    Environment variables (set before running)::

        LANGCHAIN_TRACING_V2=true
        LANGCHAIN_API_KEY=<your-key>
        LANGCHAIN_PROJECT=<optional-project>   # overridden by project_name arg

    Usage::

        from idavoll.observability.langsmith_plugin import LangSmithPlugin
        from idavoll.observability import ObservabilityPlugin, configure_logging

        configure_logging()
        app.use(ObservabilityPlugin())
        app.use(LangSmithPlugin(project_name="vingolf-dev"))

    What you get in LangSmith:
        idavoll.session  (chain)          ← one per run_session() call
        ├── Alice  (llm)                  ← each LLM call, named after the agent
        ├── Bob    (llm)
        └── ...

    Can be combined freely with :class:`ObservabilityPlugin` — they are
    independent and both subscribe to the same hook bus.
    """

    name = "idavoll.langsmith"

    def __init__(self, project_name: str | None = None) -> None:
        try:
            import langsmith  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "langsmith is required for LangSmithPlugin: pip install langsmith"
            ) from exc
        self._project_name = project_name
        # session_id → RunTree
        self._runs: dict[str, Any] = {}

    def install(self, app: "IdavollApp") -> None:
        app.hooks.on("session.created", self._on_session_created)
        app.hooks.on("agent.before_generate", self._on_before_generate)
        app.hooks.on("session.closed", self._on_session_closed)

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_session_created(self, session: Session, **_: Any) -> None:
        from langsmith.run_trees import RunTree

        run = RunTree(
            name="idavoll.session",
            run_type="chain",
            inputs={
                "session_id": session.id,
                "participants": [a.profile.name for a in session.participants],
            },
            tags=["idavoll"],
            project_name=self._project_name,
        )
        run.post()
        self._runs[session.id] = run

        # Seed metadata keys — app.py reads these before every LLM call
        session.metadata["_langsmith_callbacks"] = [run.to_callback_handler()]
        session.metadata["_langsmith_tags"] = ["idavoll"]

    def _on_before_generate(self, session: Session, agent: Agent, **_: Any) -> None:
        """Refresh per-turn context so LangSmith shows the agent's name."""
        run = self._runs.get(session.id)
        if run is None:
            return
        # run_name appears as the span label in LangSmith's timeline
        session.metadata["_langsmith_run_name"] = agent.profile.name
        session.metadata["_langsmith_metadata"] = {
            "session_id": session.id,
            "agent_id": agent.id,
            "agent_name": agent.profile.name,
        }

    def _on_session_closed(self, session: Session, **_: Any) -> None:
        run = self._runs.pop(session.id, None)
        if run is None:
            return
        run.end(outputs={"message_count": len(session.messages)})
        run.patch()

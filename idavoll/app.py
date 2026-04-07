from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent.wizard import ProfileWizard

from langchain_core.language_models import BaseChatModel

from .agent.compiler import ProfileCompiler
from .agent.consolidator import MemoryConsolidator
from .agent.memory import AgentMemory
from .agent.memory_queue import MemoryWriteQueue
from .agent.registry import Agent, AgentRegistry
from .agent.repository import AgentRepository
from .config import IdavollConfig
from .llm.adapter import LLMAdapter
from .plugin.base import IdavollPlugin
from .plugin.hooks import HookBus
from .prompt.builder import PromptBuilder
from .scheduler.base import SchedulerStrategy  # re-exported for callers
from .scheduler.strategies import RoundRobinStrategy
from .session.session import Message, Session, SessionState


class SessionManager:
    """Creates and tracks all sessions within an IdavollApp instance."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        participants: list[Agent],
        metadata: dict[str, Any] | None = None,
        max_context_messages: int = 20,
        scheduler: "SchedulerStrategy | None" = None,
        per_agent_max_turns: int | None = None,
    ) -> Session:
        session = Session(
            participants=participants,
            metadata=metadata,
            max_context_messages=max_context_messages,
            scheduler=scheduler,
            per_agent_max_turns=per_agent_max_turns,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_raise(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        return session

    def all(self) -> list[Session]:
        return list(self._sessions.values())


class IdavollApp:
    """
    Top-level application object. Assembles all framework components and
    exposes them to plugins.

    Typical usage::

        app = IdavollApp(llm=ChatAnthropic(model="claude-sonnet-4-6"))
        app.use(TopicPlugin())
        app.use(ReviewPlugin())

        agent = await app.create_agent("Alice", "A curious philosopher...")
        session = app.sessions.create(participants=[agent])
        await app.run_session(session, rounds=5)
    """

    def __init__(
        self,
        llm: BaseChatModel,
        config: IdavollConfig | None = None,
        agents_dir: str | Path | None = None,
        memory_dir: str | Path | None = None,
    ) -> None:
        self._config = config or IdavollConfig()
        self.hooks = HookBus()
        self.agents = AgentRegistry()
        self.sessions = SessionManager()
        self.llm = LLMAdapter(llm)
        self.compiler = ProfileCompiler(llm)
        self.prompt_builder = PromptBuilder()
        self.scheduler: SchedulerStrategy = self._config.scheduler.build()
        self._plugins: list[IdavollPlugin] = []

        # Optional persistent memory layer — enabled by passing agents_dir
        self.repo: AgentRepository | None = (
            AgentRepository(agents_dir, memory_dir=memory_dir)
            if agents_dir is not None else None
        )
        self._memory_queue: MemoryWriteQueue | None = (
            MemoryWriteQueue(MemoryConsolidator(llm), self.repo)
            if self.repo is not None else None
        )
        if self.repo is not None:
            assert self._memory_queue is not None
            self._memory_queue.start()
            self._register_memory_hook()

    def _register_memory_hook(self) -> None:
        """Wire memory lifecycle hooks when agents_dir is configured.

        - seat.before_generate: render agent.memory into seat.local_context["_memory_context"]
          so run_session can pass it to PromptBuilder as the memory sub-part of Section 3.
          Writing to the Seat (not session.metadata) ensures per-agent isolation.
        - session.closed: enqueue each agent's consolidate+save as an independent task.
        """
        from .session.seat import Seat as _Seat

        async def on_seat_before_generate(
            seat: _Seat, agent: Agent, **_: Any
        ) -> None:
            if not agent.memory.entries:
                return
            memory_text = agent.memory.to_context_text(
                agent.profile.memory_plan,
                agent.profile.budget.memory_context_max,
            )
            if memory_text:
                seat.local_context["_memory_context"] = memory_text

        async def on_session_closed(session: Session, **_: Any) -> None:
            assert self._memory_queue is not None
            for agent in session.participants:
                await self._memory_queue.enqueue(agent, session)
            await self._memory_queue.flush()

        self.hooks.on("seat.before_generate", on_seat_before_generate)
        self.hooks.on("session.closed", on_session_closed)

    @classmethod
    def from_config(cls, config: IdavollConfig, api_key: str | None = None) -> "IdavollApp":
        """
        Build an IdavollApp entirely from a config object.

        The LLM is constructed via ``config.llm.build(api_key)``.
        Set *api_key* explicitly or rely on the environment variable
        expected by the provider (e.g. ``ANTHROPIC_API_KEY``).
        """
        llm = config.llm.build(api_key=api_key)
        return cls(llm=llm, config=config)

    # ── Plugin management ────────────────────────────────────────────────────

    def use(self, plugin: IdavollPlugin) -> "IdavollApp":
        """Register a plugin. Returns self for chaining: app.use(A()).use(B())."""
        plugin.install(self)
        self._plugins.append(plugin)
        return self
    
    def use_all(self, *plugins: IdavollPlugin | list[IdavollPlugin]) -> "IdavollApp":
        """Register multiple plugins at once."""
        for plugin in plugins:
            self.use(plugin)
        return self

    # ── High-level agent API ─────────────────────────────────────────────────

    def create_wizard(self, name: str) -> "ProfileWizard":
        """
        Return a ProfileWizard for interactive Agent creation.

        The wizard drives a multi-turn guided dialogue (Identity → Voice →
        Confirm).  Once the user confirms, register the profile and optionally
        export Agents.md::

            wizard = app.create_wizard("李明")
            resp = wizard.start()
            while resp.phase != WizardPhase.DONE:
                resp = await wizard.reply(input("> "))
            agent = app.agents.register(resp.profile)
            wizard.export_agents_md("example/Agents.md")
        """
        from .agent.wizard import ProfileWizard
        return ProfileWizard(name=name, llm=self.llm.raw)

    async def create_agent(self, name: str, description: str) -> Agent:
        """
        Compile a natural language description into an AgentProfile, register
        the resulting agent, and save its yaml if a repo is configured.
        """
        profile = await self.compiler.compile(name, description)
        agent = self.agents.register(profile)
        if self.repo is not None:
            self.repo.save(agent)
        await self.hooks.emit("agent.created", agent=agent)
        return agent

    def load_agent(self, path: str | Path) -> Agent:
        """
        Load an agent from an existing yaml file and register it.

        Use this to restore agents with accumulated long-term memory across
        sessions::

            agent = app.load_agent("agents/professor.yaml")
        """
        if self.repo is None:
            raise RuntimeError(
                "agents_dir is not configured — pass agents_dir to IdavollApp() to enable persistence."
            )
        profile, memory = self.repo.load(path)
        agent = self.agents.register(profile, memory)
        return agent

    # ── Session participant management ───────────────────────────────────────

    async def join_session(self, session: Session, agent: Agent) -> None:
        """Add *agent* to an active session and emit ``session.agent.joined``.

        Idempotent: calling with an already-active participant is a no-op.
        If the agent previously left (seat state LEFT) they are re-activated.
        """
        session.add_participant(agent)
        await self.hooks.emit("session.agent.joined", session=session, agent=agent)

    async def leave_session(self, session: Session, agent: Agent) -> None:
        """Permanently remove *agent* from the session and emit ``session.agent.left``.

        The agent's seat is preserved for history (state → LEFT) but they will
        never be scheduled again unless they re-join via :meth:`join_session`.
        """
        session.remove_participant(agent)
        await self.hooks.emit("session.agent.left", session=session, agent=agent)

    async def pause_agent(self, session: Session, agent: Agent) -> None:
        """Temporarily suspend *agent* from being scheduled.

        Emits ``session.agent.paused``.  Call :meth:`resume_agent` to restore.
        """
        session.pause_participant(agent)
        await self.hooks.emit("session.agent.paused", session=session, agent=agent)

    async def resume_agent(self, session: Session, agent: Agent) -> None:
        """Resume a previously paused *agent* and emit ``session.agent.resumed``.

        If the agent was never added (or has state LEFT) they are treated as a
        fresh join — equivalent to :meth:`join_session`.
        """
        session.resume_participant(agent)
        await self.hooks.emit("session.agent.resumed", session=session, agent=agent)

    # ── Scheduling loop ──────────────────────────────────────────────────────

    async def run_session(
        self,
        session: Session,
        rounds: int | None = None,
        min_interval: float | None = None,
    ) -> None:
        """
        Drive a session through `rounds` turns of agent speech.

        Each turn:
          1. Scheduler picks the next agent.
          2. PromptBuilder assembles the message list.
          3. Plugins can modify context via `session.message.before`.
          4. LLM generates a response.
          5. Message is stored and `session.message.after` is emitted.
          6. Sleep for `min_interval` seconds before the next turn.

        The loop exits early if the scheduler returns should_continue=False
        (e.g. when a plugin closes the session via session.close()).
        """
        _rounds = rounds if rounds is not None else self._config.session.default_rounds
        _interval = min_interval if min_interval is not None else self._config.session.min_interval

        session.state = SessionState.ACTIVE
        await self.hooks.emit("session.created", session=session)

        # Use the session-level scheduler when set, fall back to app default.
        _scheduler = session.scheduler or self.scheduler

        for _ in range(_rounds):
            if not _scheduler.should_continue(session):
                break

            agent = _scheduler.select_next(session, session.participants)
            await self.hooks.emit("scheduler.selected", session=session, agent=agent)

            # forum.before_turn: plugins write forum-level shared context into
            # session.metadata (e.g. topic description, debate rules).
            await self.hooks.emit("forum.before_turn", session=session, agent=agent)

            # seat.before_generate: plugins write per-agent context into
            # seat.local_context (e.g. _memory_context, reply hints).
            # Keeping the contexts in the Seat prevents cross-agent contamination
            # when multiple agents generate concurrently.
            _seat = session.seats[agent.id]
            await self.hooks.emit(
                "seat.before_generate", seat=_seat, session=session, agent=agent
            )

            memory_context: str = _seat.local_context.pop("_memory_context", "")
            scene_context: str = _seat.local_context.get("scene_context", "")

            await self.hooks.emit(
                "session.message.before", session=session, agent=agent
            )

            messages = self.prompt_builder.build(
                agent, session, scene_context, memory_context
            )
            _t0 = time.monotonic()
            content = await self.llm.generate(
                messages,
                callbacks=session.metadata.get("_langsmith_callbacks"),
                run_name=session.metadata.get("_langsmith_run_name"),
                metadata=session.metadata.get("_langsmith_metadata"),
                tags=session.metadata.get("_langsmith_tags"),
            )
            _latency_ms = (time.monotonic() - _t0) * 1000
            await self.hooks.emit(
                "llm.generate.after",
                agent=agent,
                session=session,
                latency_ms=_latency_ms,
                content_length=len(content),
            )

            message = Message(
                agent_id=agent.id,
                agent_name=agent.profile.name,
                content=content,
            )
            session.add_message(message)

            await self.hooks.emit(
                "session.message.after", session=session, message=message
            )

            # Per-agent turn quota: disable seat when the limit is reached.
            _seat.post_count += 1
            if _seat.max_turns is not None and _seat.post_count >= _seat.max_turns:
                session.pause_participant(agent)
                await self.hooks.emit(
                    "session.agent.quota_reached",
                    session=session,
                    agent=agent,
                    post_count=_seat.post_count,
                )

            await asyncio.sleep(_interval)

        session.close()
        await self.hooks.emit("session.closed", session=session)

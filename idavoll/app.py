from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from .agent.profile_service import AgentProfileService
from .agent.registry import Agent, AgentRegistry
from .agent.workspace import ProfileWorkspace, ProfileWorkspaceManager
from .config import IdavollConfig
from .llm.adapter import LLMAdapter
from .memory.builtin import BuiltinMemoryProvider
from .memory.cognition import GrowthResult, SelfGrowthEngine
from .memory.manager import MemoryManager
from .session.compressor import ContextCompressor
from .session.search import SessionSearch
from .skills.library import SkillsLibrary
from .plugin.base import IdavollPlugin
from .plugin.hooks import HookBus
from .prompt.compiler import PromptCompiler
from .safety.scanner import SafetyScanner
from .scheduling.scheduler import Scheduler
from .session.session import Session
from .tools.registry import ToolRegistry, ToolsetManager


JobScheduler = Scheduler


class SessionManager:
    """Owns sessions for the lifetime of an app instance."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        participants: list[Agent],
        metadata: dict[str, Any] | None = None,
        max_context_messages: int = 20,
    ) -> Session:
        session = Session(
            participants=participants,
            metadata=metadata,
            max_context_messages=max_context_messages,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_raise(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        return session

    def all(self) -> list[Session]:
        return list(self._sessions.values())


class IdavollApp:
    """Core application object shared by product layers."""

    def __init__(
        self,
        llm: BaseChatModel,
        config: IdavollConfig | None = None,
        agents_dir: str | Path | None = None,
        memory_dir: str | Path | None = None,
    ) -> None:
        del agents_dir, memory_dir

        self._config = config or IdavollConfig()
        self.hooks = HookBus()
        self.agents = AgentRegistry()
        self.sessions = SessionManager()
        self.scheduler = Scheduler(
            max_concurrent_jobs=self._config.scheduler.max_concurrent_jobs,
            default_cooldown_seconds=self._config.scheduler.default_cooldown_seconds,
        )
        self.llm = LLMAdapter(llm)
        self.profile_service = AgentProfileService(self.llm)
        self.safety_scanner = SafetyScanner()
        self.tool_registry = ToolRegistry()
        self.toolsets = ToolsetManager(self.tool_registry)
        self.prompt_compiler = PromptCompiler(
            scanner=self.safety_scanner,
            toolsets=self.toolsets,
        )
        self.workspaces = ProfileWorkspaceManager(self._config.workspace.base_dir)
        self.growth_engine = SelfGrowthEngine(self.llm, self.hooks)
        self.compressor = ContextCompressor(
            self.llm, self.hooks, self._config.compression
        )
        self._plugins: list[IdavollPlugin] = []

    @classmethod
    def from_config(cls, config: IdavollConfig, api_key: str | None = None) -> "IdavollApp":
        llm = config.llm.build(api_key=api_key)
        return cls(llm=llm, config=config)

    def use(self, plugin: IdavollPlugin) -> "IdavollApp":
        plugin.install(self)
        self._plugins.append(plugin)
        return self

    async def create_agent(self, name: str, description: str) -> Agent:
        profile, soul = await self.profile_service.compile(name, description)
        workspace = self.workspaces.get_or_create(profile, soul)
        memory = MemoryManager().add_provider(BuiltinMemoryProvider(workspace))
        skills = SkillsLibrary(workspace)
        session_search = SessionSearch(workspace)
        agent = self.agents.register(profile)
        agent.workspace = workspace
        agent.memory = memory
        agent.skills = skills
        agent.session_search = session_search
        agent.tools = self.toolsets.resolve(
            profile.enabled_toolsets,
            disabled_tools=profile.disabled_tools,
        )
        await self.hooks.emit("agent.created", agent=agent)
        return agent

    async def generate_response(
        self,
        agent: Agent,
        *,
        session: Session | None = None,
        scene_context: str = "",
        memory_context: str = "",
        current_message: str | None = None,
        system_message: str = "",
    ) -> str:
        # 1. Get or lazily compile the frozen system prompt for this agent.
        if session is not None:
            frozen = session.frozen_prompts.get(agent.id)
            if frozen is None:
                frozen = self.prompt_compiler.compile_system(
                    agent, system_message=system_message
                )
                session.frozen_prompts[agent.id] = frozen
        else:
            frozen = self.prompt_compiler.compile_system(
                agent, system_message=system_message
            )

        # 2. Compress session history if approaching context budget.
        if session is not None:
            await self.compressor.maybe_compress(agent, session)

        # 3. Auto-fetch memory context if not supplied by the caller.
        if not memory_context and agent.memory and current_message:
            memory_context = await agent.memory.prefetch(
                current_message, scene_context
            )

        # Auto-append session context (cross-session experience recall).
        if current_message and agent.session_search:
            session_ctx = agent.session_search.search(current_message, scene_context)
            if session_ctx:
                memory_context = (
                    (memory_context + "\n\n" + session_ctx)
                    if memory_context
                    else session_ctx
                )

        # 4. Build the dynamic turn messages.
        await self.hooks.emit(
            "llm.generate.before",
            agent=agent,
            session=session,
            scene_context=scene_context,
            current_message=current_message,
        )

        messages = self.prompt_compiler.build_turn(
            frozen,
            session,
            scene_context=scene_context,
            memory_context=memory_context,
            current_message=current_message,
        )

        content = await self.llm.generate(messages)

        await self.hooks.emit(
            "llm.generate.after",
            agent=agent,
            session=session,
            content=content,
        )

        # 4. Notify memory providers that this turn is done.
        if agent.memory and current_message:
            await agent.memory.sync_turn(current_message, content)

        return content

    def preview_soul(self, agent: Agent) -> str:
        """Return the current SOUL.md content for the agent.

        This is the text users see between refinement rounds so they can
        decide what to adjust next.  Returns an empty string when the agent
        has no workspace (e.g. an agent created without a persistent profile).
        """
        if agent.workspace is None:
            return ""
        return agent.workspace.read_soul()

    async def refine_soul(self, agent: Agent, feedback: str) -> str:
        """Refine the agent's SOUL.md based on user feedback and return the updated text.

        Multi-turn creation flow (§8.1 mvp_design.md):
        ------------------------------------------------
        Round 1:  ``create_agent(name, description)`` — generates initial SOUL.md
        Round N:  ``refine_soul(agent, feedback)``    — updates SOUL.md in-place

        The caller should call ``preview_soul(agent)`` after each round to show
        the user the current state before they provide the next round of feedback.

        On any LLM failure the current soul is preserved and its text is returned
        unchanged, so a failed round is always safe to retry.
        """
        if agent.workspace is None:
            raise ValueError(
                f"Agent {agent.name!r} has no workspace; call create_agent() first."
            )
        current_text = agent.workspace.read_soul()
        updated_soul = await self.profile_service.refine(
            agent.name, current_text, feedback
        )
        rendered = ProfileWorkspaceManager.render_soul(agent.profile, updated_soul)
        agent.workspace.write_soul(rendered)
        await self.hooks.emit("soul.refined", agent=agent, feedback=feedback)
        return rendered

    async def close_session(
        self,
        session: Session,
        agents: list[Agent] | None = None,
    ) -> list[GrowthResult]:
        """Close a session and run Self-Growth Engine for each agent.

        If *agents* is None, all ``Agent`` instances in
        ``session.participants`` are used.  Returns one ``GrowthResult``
        per agent.
        """
        if agents is None:
            agents = [a for a in session.participants if isinstance(a, Agent)]

        results: list[GrowthResult] = []
        for agent in agents:
            result = await self.growth_engine.run(agent, session)
            results.append(result)

        session.close()
        await self.hooks.emit("session.closed", session=session, results=results)
        return results

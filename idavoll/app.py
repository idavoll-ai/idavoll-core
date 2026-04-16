from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage

from .agent.registry import Agent, AgentLoader, AgentRegistry
from .agent.profile import (
    AgentProfile,
    BOOTSTRAP_SENTINEL,
    BOOTSTRAP_SENTINEL_END,
    BOOTSTRAP_SYSTEM,
    SoulSpec,
    build_soul_from_extracted,
    extract_soul,
    parse_soul_markdown,
    refine_soul_spec,
)
from .agent.workspace import ProfileWorkspace, ProfileWorkspaceManager

logger = logging.getLogger(__name__)
from .config import IdavollConfig
from .llm.adapter import LLMAdapter
from .memory.builtin import BuiltinMemoryProvider
from .memory.flush import flush_memories
from .memory.manager import MemoryManager
from .session.compressor import ContextCompressor
from .session.search import SessionSearch
from .skills.library import SkillsLibrary
from .hook.base import IdavollPlugin
from .hook.hooks import HookBus
from .prompt.compiler import PromptCompiler
from .safety.scanner import SafetyScanner
from .session.session import Session
from .tools.builtin import memory, reflect, session_search, skill_get, skill_patch
from .tools.registry import ToolRegistry, Toolset, ToolSpec, ToolsetManager
from .subagent.runtime import SubagentRuntime
from .subagent.tool import TASK_TOOL_SPEC_BASE, task_tool_fn



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

    def delete(self, session_id: str) -> Session | None:
        return self._sessions.pop(session_id, None)


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
        self.tool_registry = ToolRegistry()
        self.toolsets = ToolsetManager(self.tool_registry)
        self.agents = AgentRegistry(toolsets=self.toolsets)
        self.sessions = SessionManager()
        self.llm = LLMAdapter(llm)
        self.safety_scanner = SafetyScanner()
        self.prompt_compiler = PromptCompiler(
            scanner=self.safety_scanner,
            toolsets=self.toolsets,
        )
        self.workspaces = ProfileWorkspaceManager(self._config.workspace.base_dir)
        self.compressor = ContextCompressor(
            self.llm, self.hooks, self._config.compression
        )
        self._plugins: list[IdavollPlugin] = []
        self._agent_loader: AgentLoader | None = None
        self.subagent_runtime = SubagentRuntime(self)
        self._register_builtin_tools()
        self._register_builtin_hooks()

    @classmethod
    def from_config(cls, config: IdavollConfig, api_key: str | None = None) -> "IdavollApp":
        llm = config.llm.build(api_key=api_key)
        return cls(llm=llm, config=config)

    def _register_builtin_tools(self) -> None:
        """Register Core builtin tools and define their default toolsets."""
        for fn in (memory, reflect, session_search, skill_get, skill_patch):
            spec: ToolSpec = getattr(fn, "__tool_spec__")
            self.tool_registry.register(spec)

        self.toolsets.define(Toolset(
            name="memory",
            tools=["memory", "reflect", "session_search"],
            description="Agent 长期记忆管理、自主反思与历史 session 搜索",
        ))
        self.toolsets.define(Toolset(
            name="skills",
            tools=["skill_get", "skill_patch"],
            description="Agent Skill 查看与更新",
        ))
        self.toolsets.define(Toolset(
            name="builtin",
            includes=["memory", "skills"],
            description="Core 全部内置工具",
        ))

        # task_tool: _runtime pre-bound here; _agent bound later by _bind_agent_tools
        task_spec = dataclasses.replace(
            TASK_TOOL_SPEC_BASE,
            fn=functools.partial(task_tool_fn, _runtime=self.subagent_runtime),
        )
        self.tool_registry.register(task_spec)
        self.toolsets.define(Toolset(
            name="task",
            tools=["task_tool"],
            description="子任务委派能力（task_tool）",
        ))

    def _register_builtin_hooks(self) -> None:
        """Register Core built-in hook listeners."""
        llm = self.llm

        async def _on_pre_compress(agent: "Agent", session: "Session", **_: object) -> None:
            await flush_memories(agent, session, llm)

        self.hooks.on("on_pre_compress", _on_pre_compress)

    @staticmethod
    def _bind_agent_tools(agent: "Agent") -> None:
        """Bind agent-scoped services into builtin tool fns via functools.partial.

        Builtin tool functions declare ``_agent`` as a keyword-only parameter to
        signal that it should be injected at bind time rather than supplied by the
        LLM.  This method replaces each such ToolSpec with a new instance whose
        ``fn`` is a partial with ``_agent`` pre-filled, so the execution loop can
        call ``spec.fn(**llm_args)`` without any extra plumbing.
        """
        bound: list[ToolSpec] = []
        for spec in agent.tools:
            if spec.fn is not None and "_agent" in inspect.signature(spec.fn).parameters:
                bound.append(dataclasses.replace(spec, fn=functools.partial(spec.fn, _agent=agent)))
            else:
                bound.append(spec)
        agent.tools = bound

    def unlock_toolset(self, agent: "Agent", toolset_name: str) -> "Agent":
        """Unlock a toolset for an agent and re-bind agent-scoped tool fns.

        Wraps ``AgentRegistry.unlock_toolset`` so that builtin tools (which
        declare ``_agent`` in their signature) are re-bound with the correct
        agent reference after every tool resolution.
        """
        self.agents.unlock_toolset(agent.id, toolset_name)
        self._bind_agent_tools(agent)
        return agent

    async def create_session(
        self,
        participants: list["Agent"],
        metadata: dict[str, Any] | None = None,
        max_context_messages: int = 20,
    ) -> Session:
        """Create a session and emit ``on_session_start``.

        Prefer this over ``app.sessions.create()`` so plugins that listen on
        ``on_session_start`` are notified.  ``app.sessions.create()`` remains
        available for cases where no hook notification is needed (e.g. tests).
        """
        session = self.sessions.create(
            participants=participants,
            metadata=metadata,
            max_context_messages=max_context_messages,
        )
        await self.hooks.emit("on_session_start", session=session, participants=participants)
        return session

    def set_agent_loader(self, loader: AgentLoader) -> "IdavollApp":
        """Register a loader that restores AgentProfiles from external storage.

        Called by product plugins (e.g. Vingolf) during ``install()``.
        Returns *self* for chaining.
        """
        self._agent_loader = loader
        return self

    def use(self, plugin: IdavollPlugin) -> "IdavollApp":
        plugin.install(self)
        self._plugins.append(plugin)
        return self

    def _attach_runtime(self, agent: Agent, workspace: ProfileWorkspace) -> None:
        """Attach workspace-backed services to a freshly registered agent."""
        agent.workspace = workspace
        agent.memory = MemoryManager().add_provider(BuiltinMemoryProvider(workspace))
        agent.skills = SkillsLibrary(workspace)
        agent.session_search = SessionSearch()
        agent.tools = self.toolsets.resolve(
            agent.profile.enabled_toolsets,
            disabled_tools=agent.profile.disabled_tools,
        )
        self._bind_agent_tools(agent)

    async def create_agent(self, name: str, description: str) -> Agent:
        soul = await extract_soul(self.llm, name, description)
        profile = AgentProfile(name=name, description=description.strip())
        workspace = self.workspaces.get_or_create(profile, soul)
        agent = self.agents.register(profile)
        self._attach_runtime(agent, workspace)
        await self.hooks.emit("agent.created", agent=agent)
        return agent

    async def create_agent_from_soul(
        self,
        name: str,
        description: str,
        soul_text: str,
    ) -> Agent:
        """Create an agent directly from a confirmed SOUL.md text.

        This is the bridge between the bootstrap conversation and persistent
        agent creation: once the user confirms a generated SOUL.md draft, the
        caller can persist that exact persona instead of re-generating from the
        free-form description.
        """
        self.safety_scanner.scan(soul_text, source="SOUL.md")
        soul = parse_soul_markdown(soul_text)
        profile = AgentProfile(name=name, description=description.strip())
        workspace = self.workspaces.get_or_create(profile, soul)
        # Normalize the stored SOUL.md to the canonical project format.
        workspace.write_soul_spec(profile, soul)
        agent = self.agents.register(profile)
        self._attach_runtime(agent, workspace)
        await self.hooks.emit("agent.created", agent=agent)
        return agent

    async def load_agent(self, agent_id: str) -> Agent | None:
        """Return the agent from cache, or restore it via the registered loader.

        Returns *None* when:
        - no loader has been registered, or
        - the loader does not recognise *agent_id*, or
        - the agent's workspace directory is missing on disk.

        The Vingolf layer (or any product plugin) should call
        ``set_agent_loader()`` during ``install()`` to wire up its storage
        backend before this method is used.
        """
        agent = self.agents.get(agent_id)
        if agent is not None:
            return agent

        if self._agent_loader is None:
            return None

        profile = await self._agent_loader(agent_id)
        if profile is None:
            return None

        try:
            workspace = self.workspaces.load(profile.id)
        except FileNotFoundError:
            return None

        agent = self.agents.register(profile)
        self._attach_runtime(agent, workspace)
        await self.hooks.emit("agent.loaded", agent=agent)
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
            session_ctx = await agent.session_search.search(current_message, scene_context)
            if session_ctx:
                memory_context = (
                    (memory_context + "\n\n" + session_ctx)
                    if memory_context
                    else session_ctx
                )

        # 4. Build the dynamic turn messages.
        await self.hooks.emit(
            "pre_llm_call",
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

        # 5. Tool execution loop.
        # If the agent has callable tools, bind them natively so the LLM can
        # emit tool_calls.  We loop until the model stops calling tools (or
        # hits the safety cap), firing pre_tool_call / post_tool_call hooks on
        # each invocation.  Falls back to a plain generate() when no callable
        # tools are configured so the hot path is unchanged.
        callable_tools = [t for t in agent.tools if t.fn is not None]

        if callable_tools:
            # Index by name so dispatch uses the agent-bound (partial) specs,
            # not the raw registry entries which lack the _agent injection.
            tool_index = {t.name: t for t in callable_tools}
            loop_messages = list(messages)
            ai_message: AIMessage | None = None
            for _ in range(10):  # safety cap: at most 10 tool-call rounds
                ai_message = await self.llm.invoke(loop_messages, tools=callable_tools)
                if not getattr(ai_message, "tool_calls", None):
                    break
                loop_messages.append(ai_message)
                for tc in ai_message.tool_calls:
                    t_name = tc["name"]
                    t_args = tc["args"]
                    t_id = tc["id"]
                    await self.hooks.emit(
                        "pre_tool_call",
                        agent=agent,
                        session=session,
                        tool_name=t_name,
                        tool_args=t_args,
                    )
                    spec = tool_index.get(t_name)
                    if spec is not None and spec.fn is not None:
                        try:
                            result = spec.fn(**t_args)
                            if inspect.isawaitable(result):
                                result = await result
                            result_str = str(result)
                        except Exception as exc:  # noqa: BLE001
                            result_str = f"[tool error] {exc}"
                    else:
                        result_str = f"[tool {t_name!r} not available]"
                    await self.hooks.emit(
                        "post_tool_call",
                        agent=agent,
                        session=session,
                        tool_name=t_name,
                        tool_args=t_args,
                        result=result_str,
                    )
                    loop_messages.append(ToolMessage(content=result_str, tool_call_id=t_id))
            content = str(ai_message.content) if ai_message and ai_message.content else ""
        else:
            content = await self.llm.generate(messages)

        await self.hooks.emit(
            "post_llm_call",
            agent=agent,
            session=session,
            content=content,
        )

        # 4. Notify memory providers that this turn is done.
        if agent.memory and current_message:
            await agent.memory.sync_turn(current_message, content)

        return content

    async def generate_response_stream(
        self,
        agent: Agent,
        *,
        session: Session | None = None,
        scene_context: str = "",
        memory_context: str = "",
        current_message: str | None = None,
        system_message: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streaming variant of generate_response — yields text tokens one by one.

        When callable tools are configured the tool-execution loop runs
        synchronously (tool calls can't be streamed), then the final LLM
        reply is yielded as a single chunk.  When no tools are configured
        the response is streamed token by token from the first token.

        Post-LLM hooks and memory sync are guaranteed to run after the last
        token (or on early generator close via try/finally).
        """
        # 1. Frozen system prompt.
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

        # 2. Context compression.
        if session is not None:
            await self.compressor.maybe_compress(agent, session)

        # 3. Memory context.
        if not memory_context and agent.memory and current_message:
            memory_context = await agent.memory.prefetch(
                current_message, scene_context
            )

        if current_message and agent.session_search:
            session_ctx = agent.session_search.search(current_message, scene_context)
            if session_ctx:
                memory_context = (
                    (memory_context + "\n\n" + session_ctx)
                    if memory_context
                    else session_ctx
                )

        # 4. Build turn messages.
        await self.hooks.emit(
            "pre_llm_call",
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

        # 5. Tool loop or direct streaming.
        callable_tools = [t for t in agent.tools if t.fn is not None]
        content = ""

        try:
            if callable_tools:
                tool_index = {t.name: t for t in callable_tools}
                loop_messages = list(messages)
                ai_message: AIMessage | None = None
                for _ in range(10):
                    ai_message = await self.llm.invoke(
                        loop_messages, tools=callable_tools
                    )
                    if not getattr(ai_message, "tool_calls", None):
                        break
                    loop_messages.append(ai_message)
                    for tc in ai_message.tool_calls:
                        t_name = tc["name"]
                        t_args = tc["args"]
                        t_id = tc["id"]
                        await self.hooks.emit(
                            "pre_tool_call",
                            agent=agent,
                            session=session,
                            tool_name=t_name,
                            tool_args=t_args,
                        )
                        spec = tool_index.get(t_name)
                        if spec is not None and spec.fn is not None:
                            try:
                                result = spec.fn(**t_args)
                                if inspect.isawaitable(result):
                                    result = await result
                                result_str = str(result)
                            except Exception as exc:  # noqa: BLE001
                                result_str = f"[tool error] {exc}"
                        else:
                            result_str = f"[tool {t_name!r} not available]"
                        await self.hooks.emit(
                            "post_tool_call",
                            agent=agent,
                            session=session,
                            tool_name=t_name,
                            tool_args=t_args,
                            result=result_str,
                        )
                        loop_messages.append(
                            ToolMessage(content=result_str, tool_call_id=t_id)
                        )
                content = str(ai_message.content) if ai_message and ai_message.content else ""
                if content:
                    yield content
            else:
                async for token in self.llm.astream(messages):
                    content += token
                    yield token
        finally:
            await self.hooks.emit(
                "post_llm_call",
                agent=agent,
                session=session,
                content=content,
            )
            if agent.memory and current_message:
                await agent.memory.sync_turn(current_message, content)

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
        updated_soul = await refine_soul_spec(self.llm, agent.name, current_text, feedback)
        rendered = ProfileWorkspaceManager.render_soul(agent.profile, updated_soul)
        agent.workspace.write_soul(rendered)
        await self.hooks.emit("soul.refined", agent=agent, feedback=feedback)
        return rendered

    async def refine_soul_stateless(
        self,
        name: str,
        current_soul_text: str,
        feedback: str,
    ) -> SoulSpec:
        """Stateless soul refinement — no agent required (used in preview flow)."""
        return await refine_soul_spec(self.llm, name, current_soul_text, feedback)

    async def bootstrap_chat(
        self,
        name: str,
        messages: list[dict],
    ) -> tuple[str, str | None]:
        """Drive one turn of the bootstrap conversation.

        Returns ``(reply, soul_text)`` where ``soul_text`` is the extracted
        SOUL.md content when the designer has collected enough information,
        otherwise ``None``.
        """
        from langchain_core.messages import AIMessage as _AI, HumanMessage as _H, SystemMessage as _S

        lc = [_S(content=BOOTSTRAP_SYSTEM.replace("{name}", name))]
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            lc.append(_H(content=content) if role == "user" else _AI(content=content))

        try:
            raw: str = await self.llm.generate(lc, run_name="agent-bootstrap-chat")
        except Exception:
            logger.exception("bootstrap_chat: LLM call failed for %r", name)
            return ("抱歉，出了点问题，请重试。", None)

        soul_text: str | None = None
        reply = raw.strip()
        if BOOTSTRAP_SENTINEL in raw:
            m = __import__("re").search(r"<SOUL>(.*?)</SOUL>", raw, __import__("re").DOTALL)
            if m:
                soul_text = m.group(1).strip()
                reply = raw[: raw.index(BOOTSTRAP_SENTINEL)].strip()
                if not reply:
                    reply = "已为你生成 SOUL.md 草稿，请查看右侧预览！"
        return (reply, soul_text)

    async def bootstrap_chat_stream(
        self,
        name: str,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Streaming variant of bootstrap_chat — yields SSE lines.

        Event shapes:
        * ``{"type": "token",  "delta": "..."}``
        * ``{"type": "soul",   "text":  "..."}``
        * ``{"type": "error",  "message": "..."}``
        * ``{"type": "done"}``
        """
        from langchain_core.messages import AIMessage as _AI, HumanMessage as _H, SystemMessage as _S

        def _sse(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        lc = [_S(content=BOOTSTRAP_SYSTEM.replace("{name}", name))]
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            lc.append(_H(content=content) if role == "user" else _AI(content=content))

        HOLD = len(BOOTSTRAP_SENTINEL) - 1
        hold = ""
        soul_buf: str | None = None

        try:
            async for token in self.llm.astream(lc, run_name="agent-bootstrap-stream"):
                if soul_buf is not None:
                    soul_buf += token
                    if BOOTSTRAP_SENTINEL_END in soul_buf:
                        soul_text = soul_buf[: soul_buf.index(BOOTSTRAP_SENTINEL_END)].strip()
                        yield _sse({"type": "soul", "text": soul_text})
                        soul_buf = None
                        break
                else:
                    hold += token
                    if BOOTSTRAP_SENTINEL in hold:
                        pre = hold[: hold.index(BOOTSTRAP_SENTINEL)]
                        if pre:
                            yield _sse({"type": "token", "delta": pre})
                        soul_buf = hold[hold.index(BOOTSTRAP_SENTINEL) + len(BOOTSTRAP_SENTINEL) :]
                        hold = ""
                    elif len(hold) > HOLD:
                        safe, hold = hold[:-HOLD], hold[-HOLD:]
                        yield _sse({"type": "token", "delta": safe})

            if soul_buf is None and hold:
                yield _sse({"type": "token", "delta": hold})

        except Exception:
            logger.exception("bootstrap_chat_stream: failed for %r", name)
            yield _sse({"type": "error", "message": "抱歉，出了点问题，请重试。"})

        yield _sse({"type": "done"})

    async def close_session(self, session: Session) -> None:
        """Close a session and emit ``on_session_end``.

        Core does not generate file-based session summaries anymore.
        Product layers can persist the raw transcript (for example into SQLite)
        and perform retrieval/summarization on demand.
        """
        session.close()
        await self.hooks.emit("on_session_end", session=session)

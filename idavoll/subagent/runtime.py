"""Subagent runtime — ephemeral child-agent execution.

Design constraints (§6–9, review_full_design.md):
- Subagents are existing Agent instances in a restricted runtime mode.
  No new Subagent class is introduced.
- Fresh context: child never inherits parent session history.
- Tool isolation: blocked tools are stripped before child is spawned.
- Depth limit: DEFAULT_MAX_DEPTH = 1 (reviewer cannot spawn reviewer).
- Concurrency limit: asyncio.Semaphore on _run_subagents_in_parallel.
- Timeout: asyncio.wait_for wraps every child execution.
- Interrupt: wait_for cancellation propagates to generate_response coroutine.
- Memory: child.memory is None when memory_mode == "disabled".
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .models import SubagentResult, SubagentSpec, TaskToolRequest, TaskToolResult

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..app import IdavollApp

logger = logging.getLogger(__name__)

# Tools that subagents can never call (prevents recursion and side-effects).
_DEFAULT_BLOCKED: frozenset[str] = frozenset([
    "memory",
    "skill_patch",
    "clarify",
    "send_message",
    "task_tool",
])


class SubagentRuntime:
    """Manages ephemeral subagent lifecycle within a single IdavollApp."""

    DEFAULT_MAX_DEPTH: int = 1
    DEFAULT_MAX_CONCURRENT: int = 4

    def __init__(self, app: "IdavollApp") -> None:
        self._app = app
        self._semaphore = asyncio.Semaphore(self.DEFAULT_MAX_CONCURRENT)

    # ------------------------------------------------------------------
    # Public interface (§8.1 review_full_design.md)
    # ------------------------------------------------------------------

    async def task_tool(
        self,
        parent_agent: "Agent",
        request: TaskToolRequest,
        *,
        scene_context: str = "",
    ) -> TaskToolResult:
        """Run a single delegated task and return a structured result.

        This is the only method product layers and orchestrators should call.
        Internal helpers (_spawn_subagent, _run_subagents_in_parallel) are
        reserved for SubagentRuntime's own use.
        """
        spec = SubagentSpec(
            goal=request.goal,
            context=request.context,
            role=request.role,
            toolsets=request.toolsets or [],
            blocked_tools=request.blocked_tools or [],
            memory_mode=request.memory_mode,
            max_turns=request.max_turns,
            timeout_seconds=request.timeout_seconds,
        )
        result = await self._run_subagent(parent_agent, spec, scene_context=scene_context)
        return TaskToolResult(
            status=result.status,
            summary=result.summary,
            output_text=result.output_text,
            parsed_output=result.parsed_output,
            error=result.error,
            duration_seconds=result.duration_seconds,
            tokens_used=result.tokens_used,
            tool_trace=result.tool_trace,
            child_session_id=result.child_session_id,
        )

    # ------------------------------------------------------------------
    # Internal: parallel execution (§8.3)
    # ------------------------------------------------------------------

    async def _run_subagents_in_parallel(
        self,
        parent_agent: "Agent",
        specs: list[SubagentSpec],
        *,
        scene_context: str = "",
    ) -> list[SubagentResult]:
        """Run multiple subagents concurrently, capped by DEFAULT_MAX_CONCURRENT."""

        async def _limited(spec: SubagentSpec) -> SubagentResult:
            async with self._semaphore:
                return await self._run_subagent(
                    parent_agent, spec, scene_context=scene_context
                )

        return list(await asyncio.gather(*[_limited(s) for s in specs]))

    # ------------------------------------------------------------------
    # Internal: single subagent execution
    # ------------------------------------------------------------------

    async def _run_subagent(
        self,
        parent_agent: "Agent",
        spec: SubagentSpec,
        *,
        scene_context: str = "",
    ) -> SubagentResult:
        parent_depth = parent_agent.metadata.get("delegate_depth", 0)
        if parent_depth >= self.DEFAULT_MAX_DEPTH:
            return SubagentResult(
                status="failed",
                summary="",
                output_text="",
                error=(
                    f"Max subagent depth ({self.DEFAULT_MAX_DEPTH}) exceeded. "
                    "Subagents cannot spawn further subagents."
                ),
            )

        child = self._spawn_subagent(parent_agent, spec)
        system_message = self._build_child_prompt(spec)

        # Merge goal + context into a single user turn.
        user_turn = spec.goal
        if spec.context:
            user_turn = f"{spec.goal}\n\n---\n{spec.context}"

        start = time.monotonic()
        try:
            output = await asyncio.wait_for(
                self._app.generate_response(
                    child,
                    session=None,
                    scene_context=scene_context,
                    current_message=user_turn,
                    system_message=system_message,
                ),
                timeout=spec.timeout_seconds,
            )
            duration = time.monotonic() - start
            await self._app.hooks.emit(
                "subagent.completed",
                parent_agent=parent_agent,
                child_agent=child,
                output=output,
            )
            return SubagentResult(
                status="ok",
                summary=output[:300],
                output_text=output,
                duration_seconds=round(duration, 3),
                child_session_id=child.id,
            )

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.warning(
                "subagent %r timed out after %.1fs (parent=%r)",
                child.name, spec.timeout_seconds, parent_agent.name,
            )
            await self._app.hooks.emit(
                "subagent.failed",
                parent_agent=parent_agent,
                child_agent=child,
                reason="timeout",
            )
            return SubagentResult(
                status="timeout",
                summary="",
                output_text="",
                error=f"Task timed out after {spec.timeout_seconds}s",
                duration_seconds=round(duration, 3),
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.exception(
                "subagent %r failed (parent=%r): %s",
                child.name, parent_agent.name, exc,
            )
            await self._app.hooks.emit(
                "subagent.failed",
                parent_agent=parent_agent,
                child_agent=child,
                reason=str(exc),
            )
            return SubagentResult(
                status="failed",
                summary="",
                output_text="",
                error=str(exc),
                duration_seconds=round(duration, 3),
            )

    # ------------------------------------------------------------------
    # Internal: child spawn
    # ------------------------------------------------------------------

    def _spawn_subagent(self, parent_agent: "Agent", spec: SubagentSpec) -> "Agent":
        """Create an ephemeral Agent configured for subagent execution.

        The child reuses the existing Agent class.  Its runtime mode is
        recorded in metadata; no new Subagent class is introduced (§9.1).
        """
        from ..agent.profile import AgentProfile
        from ..agent.registry import Agent

        parent_depth = parent_agent.metadata.get("delegate_depth", 0)
        child_metadata: dict[str, Any] = {
            "runtime_mode": "subagent",
            "parent_agent_id": parent_agent.id,
            "delegate_depth": parent_depth + 1,
            "memory_mode": spec.memory_mode,
        }
        if spec.role:
            child_metadata["review_role"] = spec.role

        role_name = spec.role or f"worker-{parent_agent.name}"
        child_profile = AgentProfile(name=role_name, description=spec.goal[:120])
        child = Agent(profile=child_profile, metadata=child_metadata)

        # memory_mode=disabled → child.memory stays None (default).
        # memory_mode=readonly → attach memory but block write tools via blocked_tools.

        # Resolve tools from the global registry (fresh specs, not parent's
        # already-bound partials — avoids double-binding _agent).
        child.tools = self._resolve_child_tools(parent_agent, spec)

        # Bind _agent for any tools that declare the injection point.
        self._app._bind_agent_tools(child)

        logger.debug(
            "spawned subagent %r depth=%d tools=[%s]",
            child.name,
            parent_depth + 1,
            ", ".join(t.name for t in child.tools),
        )
        return child

    def _resolve_child_tools(
        self, parent_agent: "Agent", spec: SubagentSpec
    ) -> list:
        """Return fresh ToolSpecs for the child with blocked tools removed.

        Tool resolution order (§7.2 review_full_design.md):
        1. Inherit parent's tool names (re-fetched fresh from registry).
        2. Merge any explicitly requested toolsets.
        3. Intersect with _DEFAULT_BLOCKED ∪ spec.blocked_tools (remove).
        """
        all_blocked = _DEFAULT_BLOCKED | set(spec.blocked_tools)

        candidate_names: list[str] = []
        if spec.inherit_parent_tools:
            candidate_names = [t.name for t in parent_agent.tools]

        if spec.toolsets:
            for ts_spec in self._app.toolsets.resolve(spec.toolsets):
                if ts_spec.name not in candidate_names:
                    candidate_names.append(ts_spec.name)

        result = []
        for name in candidate_names:
            if name in all_blocked:
                continue
            fresh = self._app.tool_registry.get(name)
            if fresh is not None:
                result.append(fresh)
        return result

    # ------------------------------------------------------------------
    # Internal: child system prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_child_prompt(spec: SubagentSpec) -> str:
        """Build a focused system prompt for an ephemeral subagent.

        If spec.system_instruction is provided it is used verbatim, giving
        callers (e.g. ReviewTeam) full control over the child's persona and
        output format.  Otherwise a minimal prompt is derived from spec.role.
        """
        if spec.system_instruction:
            return spec.system_instruction
        lines: list[str] = []
        if spec.role:
            lines.append(f"你的角色是 **{spec.role}**。")
        else:
            lines.append("你是一个专注于单一子任务的 Agent。")
        lines.append(
            "分析范围仅限于所提供的上下文，不要超出。"
            "完成后给出简洁、结构化的结论，直接呈现判断与依据。"
        )
        if spec.memory_mode == "disabled":
            lines.append("你无法读取或写入任何长期 memory，也无法派发子任务。")
        return "\n".join(lines)

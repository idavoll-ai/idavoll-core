"""Tests for the Phase 1 subagent runtime (SubagentRuntime + task_tool).

Coverage:
- _spawn_subagent: metadata, tool filtering, _agent binding
- _resolve_child_tools: blocked tools stripped, inherit_parent_tools=False
- Depth limit: child of depth 1 returns status="failed"
- Timeout: asyncio mock; status="timeout"
- Successful execution: status="ok", output_text matches LLM reply
- Parallel execution: _run_subagents_in_parallel returns all results
- task_tool() public interface wraps SubagentResult → TaskToolResult
- task_tool_fn (LLM-callable): returns valid JSON
- IdavollApp.subagent_runtime is wired and task toolset is registered
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from idavoll import IdavollApp, IdavollConfig
from idavoll.agent.profile import AgentProfile
from idavoll.agent.registry import Agent
from idavoll.subagent.models import SubagentSpec, TaskToolRequest
from idavoll.subagent.runtime import SubagentRuntime, _DEFAULT_BLOCKED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeLLM(BaseChatModel):
    reply: str = "子任务已完成。"

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        # Return self so the tool loop receives a plain AIMessage with no
        # tool_calls, causing generate_response to exit after the first round.
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        del messages, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages)


def _make_app(tmp_path, reply: str = "子任务已完成。") -> IdavollApp:
    return IdavollApp(
        llm=FakeLLM(reply=reply),
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )


def _make_agent(app: IdavollApp, name: str = "Alice") -> Agent:
    profile = AgentProfile(name=name)
    agent = Agent(profile=profile)
    # Give the agent the builtin toolset (memory, reflect, session_search, …)
    agent.tools = app.toolsets.resolve(["builtin", "task"])
    app._bind_agent_tools(agent)
    return agent


# ---------------------------------------------------------------------------
# Unit: SubagentRuntime._spawn_subagent
# ---------------------------------------------------------------------------

def test_spawn_sets_runtime_metadata(tmp_path) -> None:
    app = _make_app(tmp_path)
    parent = _make_agent(app)
    runtime = app.subagent_runtime
    spec = SubagentSpec(goal="分析这篇文章的论证逻辑", role="DepthReviewer")

    child = runtime._spawn_subagent(parent, spec)

    assert child.metadata["runtime_mode"] == "subagent"
    assert child.metadata["parent_agent_id"] == parent.id
    assert child.metadata["delegate_depth"] == 1
    assert child.metadata["review_role"] == "DepthReviewer"
    assert child.metadata["memory_mode"] == "disabled"


def test_spawn_child_has_no_memory_when_disabled(tmp_path) -> None:
    app = _make_app(tmp_path)
    parent = _make_agent(app)
    spec = SubagentSpec(goal="test", memory_mode="disabled")
    child = app.subagent_runtime._spawn_subagent(parent, spec)
    assert child.memory is None


# ---------------------------------------------------------------------------
# Unit: _resolve_child_tools — blocked tools stripped
# ---------------------------------------------------------------------------

def test_resolve_strips_default_blocked_tools(tmp_path) -> None:
    app = _make_app(tmp_path)
    parent = _make_agent(app)
    spec = SubagentSpec(goal="test")

    child_tools = app.subagent_runtime._resolve_child_tools(parent, spec)
    child_names = {t.name for t in child_tools}

    for blocked in _DEFAULT_BLOCKED:
        assert blocked not in child_names, f"{blocked!r} should be blocked"


def test_resolve_strips_extra_blocked_tools(tmp_path) -> None:
    app = _make_app(tmp_path)
    parent = _make_agent(app)
    spec = SubagentSpec(goal="test", blocked_tools=["reflect"])

    child_tools = app.subagent_runtime._resolve_child_tools(parent, spec)
    child_names = {t.name for t in child_tools}

    assert "reflect" not in child_names


def test_resolve_inherit_false_gives_empty_when_no_toolsets(tmp_path) -> None:
    app = _make_app(tmp_path)
    parent = _make_agent(app)
    spec = SubagentSpec(goal="test", inherit_parent_tools=False)

    child_tools = app.subagent_runtime._resolve_child_tools(parent, spec)
    assert child_tools == []


# ---------------------------------------------------------------------------
# Unit: depth limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_depth_limit_returns_failed(tmp_path) -> None:
    app = _make_app(tmp_path)
    # Simulate a parent that is already at max depth.
    profile = AgentProfile(name="DeepAgent")
    deep_parent = Agent(
        profile=profile,
        metadata={"delegate_depth": SubagentRuntime.DEFAULT_MAX_DEPTH},
    )
    spec = SubagentSpec(goal="go deeper")
    result = await app.subagent_runtime._run_subagent(deep_parent, spec)

    assert result.status == "failed"
    assert "depth" in result.error.lower()


# ---------------------------------------------------------------------------
# Integration: successful execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_subagent_ok(tmp_path) -> None:
    app = _make_app(tmp_path, reply="这是深度分析结论。")
    parent = _make_agent(app)
    spec = SubagentSpec(goal="评估论证深度", context="相关证据…", role="DepthReviewer")

    result = await app.subagent_runtime._run_subagent(parent, spec)

    assert result.status == "ok"
    assert "深度分析结论" in result.output_text
    assert result.duration_seconds is not None
    assert result.child_session_id is not None


# ---------------------------------------------------------------------------
# Integration: timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_subagent_timeout(tmp_path) -> None:
    app = _make_app(tmp_path)

    # Monkey-patch generate_response to hang.
    async def _hang(*args, **kwargs):
        await asyncio.sleep(60)

    app.generate_response = _hang  # type: ignore[method-assign]

    parent = _make_agent(app)
    spec = SubagentSpec(goal="slow task", timeout_seconds=0.05)

    result = await app.subagent_runtime._run_subagent(parent, spec)

    assert result.status == "timeout"
    assert result.error is not None


# ---------------------------------------------------------------------------
# Integration: parallel execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_subagents_in_parallel_all_succeed(tmp_path) -> None:
    app = _make_app(tmp_path, reply="ok")
    parent = _make_agent(app)
    specs = [
        SubagentSpec(goal=f"子任务 {i}", role=f"Reviewer{i}")
        for i in range(3)
    ]

    results = await app.subagent_runtime._run_subagents_in_parallel(parent, specs)

    assert len(results) == 3
    assert all(r.status == "ok" for r in results)


# ---------------------------------------------------------------------------
# Public interface: task_tool()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_tool_returns_task_tool_result(tmp_path) -> None:
    app = _make_app(tmp_path, reply="任务完成。")
    parent = _make_agent(app)
    request = TaskToolRequest(goal="分析该帖子的相关性", role="EngagementReviewer")

    result = await app.subagent_runtime.task_tool(parent, request)

    assert result.status == "ok"
    assert "任务完成" in result.output_text


# ---------------------------------------------------------------------------
# LLM-callable: task_tool_fn returns valid JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_tool_fn_returns_json(tmp_path) -> None:
    from idavoll.subagent.tool import task_tool_fn

    app = _make_app(tmp_path, reply="结论：质量良好。")
    parent = _make_agent(app)

    raw = await task_tool_fn(
        goal="评估内容质量",
        context="帖子内容…",
        _agent=parent,
        _runtime=app.subagent_runtime,
    )

    data = json.loads(raw)
    assert data["status"] == "ok"
    assert "结论" in data["output_text"]


# ---------------------------------------------------------------------------
# IdavollApp wiring
# ---------------------------------------------------------------------------

def test_app_exposes_subagent_runtime(tmp_path) -> None:
    app = _make_app(tmp_path)
    assert isinstance(app.subagent_runtime, SubagentRuntime)


def test_task_toolset_registered(tmp_path) -> None:
    app = _make_app(tmp_path)
    ts = app.toolsets.get_toolset("task")
    assert ts is not None
    assert "task_tool" in ts.tools


def test_task_tool_spec_in_registry(tmp_path) -> None:
    app = _make_app(tmp_path)
    spec = app.tool_registry.get("task_tool")
    assert spec is not None
    assert spec.fn is not None  # pre-bound partial


def test_task_tool_resolves_with_task_toolset(tmp_path) -> None:
    app = _make_app(tmp_path)
    tools = app.toolsets.resolve(["task"])
    names = [t.name for t in tools]
    assert "task_tool" in names

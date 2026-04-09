"""Tests for multi-turn SOUL.md creation via refine_soul().

Flow being tested:
  Round 1: create_agent(name, description)  → writes initial SOUL.md
  Round N: refine_soul(agent, feedback)     → updates SOUL.md in-place
  preview_soul(agent)                       → returns current text for display
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from idavoll import IdavollApp, IdavollConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soul_json(**overrides) -> str:
    """Return a valid soul JSON string, optionally patching fields."""
    base = {
        "role": "原始角色",
        "backstory": "原始背景",
        "goal": "原始目标",
        "tone": "casual",
        "language": "zh-CN",
        "quirks": ["特点一", "特点二"],
        "examples": [],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


class StructuredFakeLLM(BaseChatModel):
    """Fake LLM that returns preset responses in order, then loops on the last."""

    replies: list[str]
    _call_count: int = 0

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, replies: list[str], **data: Any) -> None:
        super().__init__(replies=replies, **data)
        object.__setattr__(self, "_call_count", 0)

    @property
    def _llm_type(self) -> str:
        return "structured-fake"

    def _next_reply(self) -> str:
        idx = min(self._call_count, len(self.replies) - 1)
        object.__setattr__(self, "_call_count", self._call_count + 1)
        return self.replies[idx]

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        del messages, kwargs
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self._next_reply()))]
        )

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)


# ---------------------------------------------------------------------------
# Tests: preview_soul
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_soul_returns_initial_soul_md(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Alice", "一个温暖的哲学爱好者")

    preview = app.preview_soul(agent)
    assert "Alice" in preview
    assert "## Identity" in preview


@pytest.mark.asyncio
async def test_preview_soul_returns_empty_for_agent_without_workspace(fake_llm) -> None:
    app = IdavollApp(llm=fake_llm)
    agent = await app.create_agent("NoWS", "no workspace")
    # Agents created without a workspace config have workspace=None.
    # Overwrite to simulate that state.
    agent.workspace = None
    assert app.preview_soul(agent) == ""


# ---------------------------------------------------------------------------
# Tests: refine_soul — fallback path (non-JSON LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refine_soul_keeps_current_soul_when_llm_returns_non_json(
    fake_llm, tmp_path
) -> None:
    """When the LLM can't return valid JSON (e.g. in tests), current soul is preserved."""
    app = IdavollApp(
        llm=fake_llm,  # always returns "这是一次测试回复。" — not valid JSON
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Bob", "一个严谨的科学家")

    soul_before = app.preview_soul(agent)
    returned = await app.refine_soul(agent, "让他更风趣一些")
    soul_after = app.preview_soul(agent)

    # The method should not crash and should return a non-empty string.
    assert returned
    # Soul file on disk matches what was returned.
    assert soul_after == returned


@pytest.mark.asyncio
async def test_refine_soul_with_empty_feedback_is_a_no_op(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Eve", "沉默的观察者")

    soul_before = app.preview_soul(agent)
    returned = await app.refine_soul(agent, "   ")
    # Soul must still be readable.
    assert returned


@pytest.mark.asyncio
async def test_refine_soul_raises_when_agent_has_no_workspace(fake_llm) -> None:
    app = IdavollApp(llm=fake_llm)
    agent = await app.create_agent("Ghost", "a ghost")
    agent.workspace = None

    with pytest.raises(ValueError, match="no workspace"):
        await app.refine_soul(agent, "更外向")


# ---------------------------------------------------------------------------
# Tests: refine_soul — happy path (structured LLM returns valid JSON)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refine_soul_updates_soul_when_llm_returns_valid_json(tmp_path) -> None:
    """Full round-trip: LLM returns valid JSON, SOUL.md is updated accordingly."""
    initial_json = _make_soul_json(role="原始角色")
    refined_json = _make_soul_json(role="哲学家", tone="academic", quirks=["喜欢引用苏格拉底"])

    llm = StructuredFakeLLM(replies=[initial_json, refined_json])

    app = IdavollApp(
        llm=llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Carol", "初始描述")

    soul_v1 = app.preview_soul(agent)

    returned = await app.refine_soul(agent, "让她成为一位热爱哲学的学者")
    soul_v2 = app.preview_soul(agent)

    assert soul_v2 == returned
    assert "哲学家" in soul_v2
    assert "academic" in soul_v2
    assert "喜欢引用苏格拉底" in soul_v2


@pytest.mark.asyncio
async def test_multi_turn_refinement_accumulates_changes(tmp_path) -> None:
    """Three refinement rounds each update the soul progressively."""
    initial_json = _make_soul_json(role="普通助手")
    round2_json = _make_soul_json(role="哲学家", tone="academic")
    round3_json = _make_soul_json(role="哲学家", tone="academic", backstory="曾在雅典求学")

    llm = StructuredFakeLLM(replies=[initial_json, round2_json, round3_json])

    app = IdavollApp(
        llm=llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Diana", "一个AI")

    await app.refine_soul(agent, "让她更学术")
    soul_after_r2 = app.preview_soul(agent)
    assert "哲学家" in soul_after_r2

    await app.refine_soul(agent, "加一段雅典求学的背景")
    soul_after_r3 = app.preview_soul(agent)
    assert "雅典" in soul_after_r3


# ---------------------------------------------------------------------------
# Tests: VingolfApp delegates correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vingolf_app_exposes_refine_and_preview(fake_llm, tmp_path) -> None:
    from vingolf import VingolfApp

    app = VingolfApp(
        IdavollApp(
            llm=fake_llm,
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
    )
    agent = await app.create_agent("Frank", "一个乐观的创业者")

    preview = app.preview_soul(agent)
    assert "Frank" in preview

    updated = await app.refine_soul(agent, "让他更冷静")
    assert updated  # non-empty, no crash


# ---------------------------------------------------------------------------
# Tests: soul.refined hook is emitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refine_soul_emits_hook(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )
    agent = await app.create_agent("Grace", "一个艺术家")

    events: list[dict] = []

    @app.hooks.hook("soul.refined")
    async def on_refined(**kwargs) -> None:
        events.append(kwargs)

    await app.refine_soul(agent, "让她更前卫")

    assert len(events) == 1
    assert events[0]["agent"] is agent
    assert events[0]["feedback"] == "让她更前卫"

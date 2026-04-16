"""Tests for Phase 2 review team (ReviewTeam + ReviewPlugin use_team=True).

Coverage:
- ReviewerOutput parsing: valid JSON, invalid JSON, subagent failure
- ReviewTeam._build_reviewer_specs: system_instruction per role, inherit_parent_tools=False
- ReviewTeam._fallback_outcome: score average, confidence reduction on divergence
- ReviewTeam._make_directives: memory_candidate / reflection_candidate / no_action
- ReviewTeam.review_agent_in_topic: end-to-end with mocked subagent runtime
- ReviewPlugin with use_team=True: full topic close → AgentReviewResult
- AgentReviewResult carries confidence and evidence from team path
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from idavoll import IdavollApp, IdavollConfig
from idavoll.agent.profile import AgentProfile
from idavoll.agent.registry import Agent
from idavoll.subagent.models import SubagentResult
from vingolf import VingolfApp, VingolfConfig
from vingolf.config import ReviewConfig, ReviewPlanConfig, ReviewRoleConfig
from vingolf.plugins.review_team import (
    ReviewOutcome,
    ReviewTeam,
    ReviewerOutput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeLLM(BaseChatModel):
    replies: list[str]
    _idx: int = 0

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, replies: list[str], **data: Any) -> None:
        super().__init__(replies=replies, **data)
        object.__setattr__(self, "_idx", 0)

    @property
    def _llm_type(self) -> str:
        return "fake-seq"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self

    def _next(self) -> str:
        i = min(self._idx, len(self.replies) - 1)
        object.__setattr__(self, "_idx", self._idx + 1)
        return self.replies[i]

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        del messages, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._next()))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages)


def _reviewer_json(**overrides) -> str:
    base = {
        "score": 7.0,
        "confidence": 0.8,
        "evidence": ["帖子A展示了清晰的因果推理", "帖子B引用了他人观点"],
        "concerns": ["论述有时过于简短"],
        "summary": "表现良好，有一定深度",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _moderator_json(**overrides) -> str:
    base = {
        "quality_score": 7.5,
        "confidence": 0.75,
        "summary": "整体表现良好，深度和互动均有体现",
        "key_strengths": ["论证清晰", "积极互动"],
        "key_weaknesses": ["安全边界需注意"],
        "growth_priority": "medium",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _make_subagent_result(text: str, status: str = "ok") -> SubagentResult:
    return SubagentResult(status=status, summary=text[:100], output_text=text)


def _make_app(tmp_path, replies: list[str]) -> IdavollApp:
    return IdavollApp(
        llm=FakeLLM(replies=replies),
        config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
    )


def _make_orchestrator() -> Agent:
    return Agent(
        profile=AgentProfile(name="ReviewOrchestrator"),
        metadata={"delegate_depth": 0},
    )


# ---------------------------------------------------------------------------
# Unit: ReviewerOutput parsing
# ---------------------------------------------------------------------------

def test_parse_reviewer_output_valid_json(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    result = _make_subagent_result(_reviewer_json(score=8.5, confidence=0.9))
    output = team._parse_reviewer_output(result, "DepthReviewer")

    assert output.role == "DepthReviewer"
    assert output.dimension == "depth"
    assert abs(output.score - 8.5) < 0.01
    assert abs(output.confidence - 0.9) < 0.01
    assert len(output.evidence) == 2
    assert not output.parse_failed


def test_parse_reviewer_output_invalid_json(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    result = _make_subagent_result("这不是JSON")
    output = team._parse_reviewer_output(result, "EngagementReviewer")

    assert output.parse_failed
    assert output.dimension == "engagement"


def test_parse_reviewer_output_subagent_failure(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    result = SubagentResult(
        status="timeout", summary="", output_text="", error="timed out"
    )
    output = team._parse_reviewer_output(result, "SafetyReviewer")

    assert output.parse_failed
    assert output.role == "SafetyReviewer"


def test_parse_reviewer_output_clamps_values(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    raw = json.dumps({"score": 15, "confidence": -0.5, "evidence": [], "concerns": [], "summary": "x"})
    result = _make_subagent_result(raw)
    output = team._parse_reviewer_output(result, "DepthReviewer")

    assert output.score == 10.0
    assert output.confidence == 0.0


# ---------------------------------------------------------------------------
# Unit: _fallback_outcome
# ---------------------------------------------------------------------------

def test_fallback_outcome_averages_scores() -> None:
    outputs = [
        ReviewerOutput(role="DepthReviewer", dimension="depth", score=8.0, confidence=0.8),
        ReviewerOutput(role="EngagementReviewer", dimension="engagement", score=6.0, confidence=0.6),
        ReviewerOutput(role="SafetyReviewer", dimension="safety", score=9.0, confidence=0.9),
    ]
    outcome = ReviewTeam._fallback_outcome(outputs)
    assert abs(outcome.quality_score - 23.0 / 3) < 0.1
    assert outcome.growth_priority == "medium"


def test_fallback_outcome_reduces_confidence_on_divergence() -> None:
    outputs = [
        ReviewerOutput(role="A", dimension="depth", score=2.0, confidence=0.9),
        ReviewerOutput(role="B", dimension="engagement", score=9.0, confidence=0.9),
    ]
    outcome = ReviewTeam._fallback_outcome(outputs)
    # High divergence → confidence should be noticeably reduced
    assert outcome.confidence < 0.7


def test_fallback_outcome_all_failed() -> None:
    outputs = [
        ReviewerOutput(role="A", dimension="depth", parse_failed=True),
    ]
    outcome = ReviewTeam._fallback_outcome(outputs)
    assert outcome.confidence == 0.0


# ---------------------------------------------------------------------------
# Unit: _make_directives
# ---------------------------------------------------------------------------

def test_make_directives_memory_candidate_on_strengths(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    outcome = ReviewOutcome(
        quality_score=8.0,
        confidence=0.8,
        summary="优秀",
        key_strengths=["论证清晰"],
        key_weaknesses=[],
        growth_priority="high",
    )
    directives = team._make_directives(outcome, "测试话题")
    kinds = [d.kind for d in directives]
    assert "memory_candidate" in kinds


def test_make_directives_reflection_on_weaknesses(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    outcome = ReviewOutcome(
        quality_score=5.0,
        confidence=0.7,
        summary="有改进空间",
        key_strengths=[],
        key_weaknesses=["缺乏深度", "互动不足"],
        growth_priority="low",
    )
    directives = team._make_directives(outcome, "测试话题")
    kinds = [d.kind for d in directives]
    assert "reflection_candidate" in kinds


def test_make_directives_no_action_on_low_confidence(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    outcome = ReviewOutcome(quality_score=7.0, confidence=0.2)
    directives = team._make_directives(outcome, "话题")
    assert all(d.kind == "no_action" for d in directives)


# ---------------------------------------------------------------------------
# Unit: _build_reviewer_specs
# ---------------------------------------------------------------------------

def test_build_reviewer_specs_count_and_roles(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    selected_roles = team._compatible_reviewer_roles("agent_in_topic")
    specs = team._build_reviewer_specs("context_bundle", "Alice", selected_roles)
    assert len(specs) == len(selected_roles)
    roles = [s.role for s in specs]
    for expected_role, _ in selected_roles:
        assert expected_role in roles


def test_build_reviewer_specs_no_tool_inheritance(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    selected_roles = team._compatible_reviewer_roles("agent_in_topic")
    specs = team._build_reviewer_specs("ctx", "Bob", selected_roles)
    for spec in specs:
        assert spec.inherit_parent_tools is False
        assert spec.memory_mode == "disabled"
        assert spec.system_instruction  # non-empty


def test_compatible_reviewer_roles_respects_config(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    plan_config = ReviewPlanConfig(
        reviewer_roles={
            "DepthReviewer": ReviewRoleConfig(
                enabled=True,
                dimension="depth",
                criteria="depth criteria",
                target_types=["agent_in_topic", "post"],
            ),
            "SafetyReviewer": ReviewRoleConfig(
                enabled=False,
                dimension="safety",
                criteria="safety criteria",
                target_types=["agent_in_topic", "post"],
            ),
        },
        default_roles_for_agent_in_topic=["DepthReviewer", "SafetyReviewer"],
        default_roles_for_post=["DepthReviewer"],
        default_roles_for_thread=["DepthReviewer"],
    )
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig(), plan_config)

    selected = team._compatible_reviewer_roles("agent_in_topic")

    assert [name for name, _ in selected] == ["DepthReviewer"]


@pytest.mark.asyncio
async def test_lead_planner_selects_subset_when_json_valid(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    with patch.object(
        app,
        "generate_response",
        new=AsyncMock(return_value='{"selected_roles":["DepthReviewer","SafetyReviewer"],"rationale":"need depth and safety"}'),
    ):
        selected = await team._select_reviewer_roles(
            "agent_in_topic",
            subject_label="Agent Alice",
            planning_context="讨论涉及高风险建议",
        )

    assert [name for name, _ in selected] == ["DepthReviewer", "SafetyReviewer"]


@pytest.mark.asyncio
async def test_lead_planner_falls_back_when_invalid_json(tmp_path) -> None:
    app = _make_app(tmp_path, [])
    team = ReviewTeam(app, _make_orchestrator(), ReviewConfig())

    with patch.object(
        app,
        "generate_response",
        new=AsyncMock(return_value="not json"),
    ):
        selected = await team._select_reviewer_roles(
            "agent_in_topic",
            subject_label="Agent Alice",
            planning_context="普通讨论",
        )

    assert [name for name, _ in selected] == [
        "DepthReviewer",
        "EngagementReviewer",
        "SafetyReviewer",
    ]


# ---------------------------------------------------------------------------
# Integration: review_agent_in_topic with mocked subagent runtime
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_agent_in_topic_success(tmp_path) -> None:
    from vingolf.plugins.topic import Topic, Post

    # Three reviewers + one moderator call.
    reviewer_reply = _reviewer_json(score=8.0, confidence=0.85)
    moderator_reply = _moderator_json(quality_score=8.0, growth_priority="high")
    app = _make_app(tmp_path, [reviewer_reply] * 3 + [moderator_reply])
    orchestrator = _make_orchestrator()
    team = ReviewTeam(app, orchestrator, ReviewConfig())

    # Mock the parallel subagent dispatch to return canned SubagentResults.
    reviewer_results = [
        _make_subagent_result(_reviewer_json(score=8.0)),
        _make_subagent_result(_reviewer_json(score=7.5)),
        _make_subagent_result(_reviewer_json(score=9.0)),
    ]
    with patch.object(
        app.subagent_runtime,
        "_run_subagents_in_parallel",
        new=AsyncMock(return_value=reviewer_results),
    ):
        topic = Topic(session_id="test-session", title="AI 的未来", description="探讨 AI 对社会的影响")
        posts = [Post(topic_id=topic.id, author_id="a1", author_name="Alice", content="有深度的观点", source="agent")]
        all_text = "[Alice] 有深度的观点"

        outcome, reviewer_outputs = await team.review_agent_in_topic(
            topic=topic,
            agent_name="Alice",
            agent_id="a1",
            agent_posts=posts,
            all_posts_text=all_text,
        )

    assert outcome.quality_score > 0
    assert 0.0 <= outcome.confidence <= 1.0
    expected_roles = await team._select_reviewer_roles(
        "agent_in_topic",
        subject_label="Agent 「Alice」",
        planning_context=all_text,
    )
    assert len(reviewer_outputs) == len(expected_roles)
    assert outcome.growth_directives  # non-empty


# ---------------------------------------------------------------------------
# Integration: ReviewPlugin with use_team=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_plugin_team_path_produces_result(tmp_path) -> None:
    reviewer_reply = _reviewer_json(score=7.0, confidence=0.75)
    moderator_reply = _moderator_json()

    # Enough replies: agent creation (1), topic seed posts (N), then reviewers+moderator.
    replies = ["测试回复"] * 20 + [reviewer_reply] * 3 + [moderator_reply]
    config = VingolfConfig(review=ReviewConfig(use_team=True, reviewer_timeout_seconds=10.0))
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=replies),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )

    agent = await app.create_agent("Alice", "一位积极的讨论者")
    topic = await app.create_topic(
        title="技术伦理", description="讨论技术发展的伦理边界", agents=[agent]
    )
    await app.let_agent_participate(topic.id, agent)

    # Mock the subagent parallel dispatch inside the team.
    reviewer_results = [
        _make_subagent_result(_reviewer_json(score=7.0)),
        _make_subagent_result(_reviewer_json(score=6.5)),
        _make_subagent_result(_reviewer_json(score=8.0)),
    ]
    with patch.object(
        app._app.subagent_runtime,
        "_run_subagents_in_parallel",
        new=AsyncMock(return_value=reviewer_results),
    ):
        await app.close_topic(topic.id)

    summary = app.get_review(topic.id)
    assert summary is not None
    result = summary.results[0]
    assert result.agent_id == agent.id
    assert result.confidence > 0.0
    assert isinstance(result.evidence, list)
    assert result.growth_directives


@pytest.mark.asyncio
async def test_review_plugin_team_path_falls_back_on_runtime_error(tmp_path) -> None:
    """ReviewPlugin must not crash when ReviewTeam raises; deterministic fallback."""
    config = VingolfConfig(review=ReviewConfig(use_team=True))
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["回复"] * 20),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )

    agent = await app.create_agent("Bob", "一位安静的参与者")
    topic = await app.create_topic(title="哲学", description="哲学讨论", agents=[agent])
    await app.let_agent_participate(topic.id, agent)

    with patch.object(
        app._app.subagent_runtime,
        "_run_subagents_in_parallel",
        new=AsyncMock(side_effect=RuntimeError("runtime exploded")),
    ):
        await app.close_topic(topic.id)

    summary = app.get_review(topic.id)
    assert summary is not None
    result = summary.results[0]
    # Should have fallen back gracefully.
    assert result.final_score >= 0
    assert result.confidence == 0.0

"""Tests for Phase 3: ReviewRepository, HotInteractionTrigger, ConsolidationService.

Coverage:
- ReviewRepository.save_review / get_reviews_for_agent / get_pending_directives
- ReviewRepository.mark_directive_applied
- TopicPlugin.like_post emits topic.post.liked
- HotInteractionTrigger: fires review when likes >= threshold
- HotInteractionTrigger: deduplicates (same post only reviewed once)
- HotInteractionTrigger: ignores user posts
- ConsolidationService.consolidate: agent reflect-and-decide before applying
- ConsolidationService.consolidate: no_action → marks applied without reflection
- ConsolidationService.consolidate_all: spans all agents
- VingolfApp.startup wires _review_repo and consolidation
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from idavoll import IdavollApp, IdavollConfig
from idavoll.agent.profile import AgentProfile
from idavoll.subagent.models import SubagentResult
from vingolf import VingolfApp, VingolfConfig
from vingolf.config import ReviewConfig, ReviewPlanConfig, ReviewRoleConfig
from vingolf.persistence import ReviewRepository
from vingolf.persistence.database import Database
from vingolf.plugins.review import AgentReviewResult, DimensionScores
from vingolf.plugins.review_team import (
    GrowthDirective,
    ReviewOutcome,
    ReviewRecord,
    ReviewerOutput,
)
from vingolf.plugins.topic import Post
from vingolf.services import ConsolidationService
from vingolf.services.consolidation import ConsolidationDecision


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
        return "fake"

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


def _make_record(
    topic_id: str = "t1",
    agent_id: str = "a1",
    *,
    score: float = 7.5,
    confidence: float = 0.8,
    kind: str = "memory_candidate",
    priority: str = "high",
) -> ReviewRecord:
    outcome = ReviewOutcome(
        quality_score=score,
        confidence=confidence,
        summary="测试评审",
        key_strengths=["论证清晰"],
        key_weaknesses=[],
        growth_priority="medium",
        growth_directives=[
            GrowthDirective(
                kind=kind,
                priority=priority,
                content="示例反思内容",
                rationale="达到阈值",
                ttl_days=30,
            )
        ],
    )
    return ReviewRecord(
        topic_id=topic_id,
        agent_id=agent_id,
        agent_name="Alice",
        reviewer_outputs=[
            ReviewerOutput(role="DepthReviewer", dimension="depth", score=score, confidence=confidence)
        ],
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# ReviewRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_repo_save_and_query(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record()

        await repo.save_review(record)

        reviews = await repo.get_reviews_for_agent("a1")
        assert len(reviews) == 1
        assert reviews[0]["agent_id"] == "a1" or reviews[0].get("topic_id") == "t1"
        assert abs(reviews[0]["quality_score"] - 7.5) < 0.01
        assert reviews[0]["target_type"] == "agent_in_topic"
        assert reviews[0]["target_id"] == "a1"


@pytest.mark.asyncio
async def test_review_repo_pending_directives(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="memory_candidate", priority="high")
        await repo.save_review(record)

        directives = await repo.get_pending_directives("a1")
        assert len(directives) == 1
        assert directives[0]["kind"] == "memory_candidate"
        assert directives[0]["priority"] == "high"
        assert directives[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_review_repo_mark_directive_applied(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record()
        await repo.save_review(record)

        directives = await repo.get_pending_directives("a1")
        assert len(directives) == 1
        directive_id = directives[0]["id"]

        await repo.mark_directive_applied(directive_id)

        # Should now be empty (applied)
        directives_after = await repo.get_pending_directives("a1")
        assert len(directives_after) == 0


@pytest.mark.asyncio
async def test_review_repo_strategy_results(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record()
        await repo.save_review(record)

        results = await repo.get_strategy_results(record.review_id)
        assert len(results) == 1
        assert results[0]["reviewer_name"] == "DepthReviewer"
        assert results[0]["dimension"] == "depth"
        assert results[0]["status"] == "ok"
        assert isinstance(results[0]["evidence"], list)
        assert isinstance(results[0]["concerns"], list)
        assert results[0]["parse_failed"] is False


@pytest.mark.asyncio
async def test_review_repo_multiple_agents(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        await repo.save_review(_make_record(agent_id="a1"))
        await repo.save_review(_make_record(agent_id="a2", topic_id="t2"))

        a1_reviews = await repo.get_reviews_for_agent("a1")
        a2_reviews = await repo.get_reviews_for_agent("a2")
        assert len(a1_reviews) == 1
        assert len(a2_reviews) == 1

        a1_directives = await repo.get_pending_directives("a1")
        a2_directives = await repo.get_pending_directives("a2")
        assert len(a1_directives) == 1
        assert len(a2_directives) == 1


@pytest.mark.asyncio
async def test_review_repo_hydrated_records_for_agent_and_topic(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        await repo.save_review(_make_record(agent_id="a1", topic_id="topic-1"))
        await repo.save_review(_make_record(agent_id="a1", topic_id="topic-2"))

        agent_records = await repo.get_review_records_for_agent("a1")
        topic_records = await repo.get_review_records_for_topic("topic-1")

        assert len(agent_records) == 2
        assert len(topic_records) == 1
        assert agent_records[0]["strategy_results"]
        assert agent_records[0]["growth_directives"]
        assert topic_records[0]["topic_id"] == "topic-1"


# ---------------------------------------------------------------------------
# TopicPlugin.like_post
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_like_post_increments_and_emits(tmp_path) -> None:
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["回复"] * 10),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
    )
    agent = await app.create_agent("Alice", "参与者")
    topic = await app.create_topic(title="测试", description="desc", agents=[agent])
    post = await app.add_user_post(topic.id, "Alice", "hello")

    emitted = []

    @app.hooks.hook("topic.post.liked")
    async def capture(**kwargs):
        emitted.append(kwargs)

    returned = await app.like_post(topic.id, post.id)
    assert returned.likes == 1
    assert len(emitted) == 1
    assert emitted[0]["post"].id == post.id


@pytest.mark.asyncio
async def test_like_post_raises_on_unknown_post(tmp_path) -> None:
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["回复"] * 5),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
    )
    agent = await app.create_agent("Bob", "参与者")
    topic = await app.create_topic(title="测试", description="desc", agents=[agent])

    with pytest.raises(KeyError):
        await app.like_post(topic.id, "non-existent-post-id")


# ---------------------------------------------------------------------------
# HotInteractionTrigger
# ---------------------------------------------------------------------------

def _reviewer_json(**overrides) -> str:
    base = {
        "score": 7.0,
        "confidence": 0.75,
        "evidence": ["示例证据"],
        "concerns": [],
        "summary": "表现良好",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _moderator_json(**overrides) -> str:
    base = {
        "quality_score": 7.0,
        "confidence": 0.75,
        "summary": "整体良好",
        "key_strengths": ["论证清晰"],
        "key_weaknesses": [],
        "growth_priority": "medium",
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


@pytest.mark.asyncio
async def test_hot_interaction_trigger_fires_review(tmp_path) -> None:
    config = VingolfConfig(
        review=ReviewConfig(
            use_team=True,
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=2,
        ),
        db_path=str(tmp_path / "vingolf.db"),
    )
    replies = ["测试回复"] * 20
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=replies),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    await app.startup()
    try:
        agent = await app.create_agent("Alice", "参与者")
        topic = await app.create_topic(title="热帖", description="desc", agents=[agent])
        assert app.topic is not None
        post = await app.topic.add_agent_post(topic.id, agent, "精彩内容")

        reviewer_results = [
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json()),
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json()),
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json()),
        ]
        moderator_reply = _moderator_json()

        with patch.object(
            app._app.subagent_runtime,
            "_run_subagents_in_parallel",
            new=AsyncMock(return_value=reviewer_results),
        ), patch.object(
            app._app.llm, "generate", new=AsyncMock(return_value=moderator_reply)
        ):
            # First like: below threshold
            await app.like_post(topic.id, post.id)
            assert post.likes == 1

            # Second like: at threshold, should trigger review
            await app.like_post(topic.id, post.id)
            assert post.likes == 2

        assert app._review_repo is not None
        reviews = await app._review_repo.get_reviews_for_agent(agent.id)
        assert len(reviews) == 1
        assert reviews[0]["trigger_type"] == "hot_interaction"
        assert reviews[0]["target_type"] == "post"
        assert reviews[0]["target_id"] == post.id

        results = await app._review_repo.get_strategy_results(reviews[0]["id"])
        assert len(results) == 3
        assert {r["dimension"] for r in results} == {"depth", "engagement", "safety"}
        assert all(r["status"] == "ok" for r in results)
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_hot_interaction_with_thread_reviewer_persists_strategy_results(tmp_path) -> None:
    config = VingolfConfig(
        review=ReviewConfig(
            use_team=True,
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=1,
        ),
        review_plan=ReviewPlanConfig(
            reviewer_roles={
                "DepthReviewer": ReviewRoleConfig(
                    enabled=True,
                    dimension="depth",
                    criteria="depth criteria",
                    target_types=["agent_in_topic", "post", "thread"],
                ),
                "EngagementReviewer": ReviewRoleConfig(
                    enabled=True,
                    dimension="engagement",
                    criteria="engagement criteria",
                    target_types=["agent_in_topic", "post", "thread"],
                ),
                "SafetyReviewer": ReviewRoleConfig(
                    enabled=True,
                    dimension="safety",
                    criteria="safety criteria",
                    target_types=["agent_in_topic", "post", "thread"],
                ),
                "ThreadReviewer": ReviewRoleConfig(
                    enabled=True,
                    dimension="thread",
                    criteria="thread criteria",
                    target_types=["post", "thread"],
                ),
            },
            default_roles_for_post=[
                "DepthReviewer",
                "EngagementReviewer",
                "SafetyReviewer",
                "ThreadReviewer",
            ],
        ),
        db_path=str(tmp_path / "vingolf-thread.db"),
    )
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["测试回复"] * 30),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    await app.startup()
    try:
        agent = await app.create_agent("Argos", "参与者")
        topic = await app.create_topic(title="Thread", description="desc", agents=[agent])
        assert app.topic is not None
        post = await app.topic.add_agent_post(topic.id, agent, "主帖子")

        reviewer_results = [
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json(summary="depth ok")),
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json(summary="engagement ok")),
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json(summary="safety ok")),
            SubagentResult(status="ok", summary="ok", output_text=_reviewer_json(summary="thread ok")),
        ]
        with patch.object(
            app._app.subagent_runtime,
            "_run_subagents_in_parallel",
            new=AsyncMock(return_value=reviewer_results),
        ), patch.object(
            app._app.llm, "generate", new=AsyncMock(return_value=_moderator_json())
        ):
            await app.like_post(topic.id, post.id)

        assert app._review_repo is not None
        reviews = await app._review_repo.get_review_records_for_topic(topic.id)
        assert len(reviews) == 1
        assert reviews[0]["status"] == "completed"
        assert reviews[0]["error_message"] is None
        assert {r["dimension"] for r in reviews[0]["strategy_results"]} == {
            "depth",
            "engagement",
            "safety",
            "thread",
        }
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_hot_interaction_deduplicates(tmp_path) -> None:
    """Same post must only be hot-reviewed once even if it keeps getting likes."""
    config = VingolfConfig(
        review=ReviewConfig(
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=1,
        )
    )
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["ok"] * 30),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    agent = await app.create_agent("Bob", "参与者")
    topic = await app.create_topic(title="去重测试", description="desc", agents=[agent])
    post = await app.add_user_post(topic.id, "Bob", "内容")
    post.source = "agent"
    post.author_id = agent.id

    review_calls: list = []

    assert app.review is not None
    original_review_post = app.review._review_post

    async def counting_review(*args, **kwargs):
        review_calls.append(1)
        return await original_review_post(*args, **kwargs)

    app.review._review_post = counting_review  # type: ignore[method-assign]

    # Like 3 times — should only trigger once.
    for _ in range(3):
        await app.like_post(topic.id, post.id)

    assert len(review_calls) == 1


@pytest.mark.asyncio
async def test_hot_interaction_ignores_user_posts(tmp_path) -> None:
    config = VingolfConfig(
        review=ReviewConfig(
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=1,
        )
    )
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["ok"] * 10),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    agent = await app.create_agent("Carol", "参与者")
    topic = await app.create_topic(title="用户帖子", description="desc", agents=[agent])
    # user post (source="user")
    post = await app.add_user_post(topic.id, "Carol", "用户说")
    assert post.source == "user"

    review_calls: list = []
    assert app.review is not None
    original_review_post = app.review._review_post

    async def counting_review(*args, **kwargs):
        review_calls.append(1)
        return await original_review_post(*args, **kwargs)

    app.review._review_post = counting_review  # type: ignore[method-assign]

    await app.like_post(topic.id, post.id)
    assert len(review_calls) == 0


def _make_hot_review_result(post: Post) -> AgentReviewResult:
    return AgentReviewResult(
        agent_id=post.author_id,
        agent_name=post.author_name,
        target_type="post",
        target_id=post.id,
        post_count=1,
        likes_count=post.likes,
        composite_score=7.0,
        likes_score=8.0,
        final_score=7.4,
        dimensions=DimensionScores(relevance=7.0, depth=7.0, originality=7.0, engagement=7.0),
        summary="单帖表现良好",
        confidence=0.7,
        growth_priority="medium",
        growth_directives=[
            GrowthDirective(
                kind="memory_candidate",
                priority="medium",
                content="保持清晰表达",
                rationale="单帖互动质量较高",
                ttl_days=14,
            )
        ],
    )


@pytest.mark.asyncio
async def test_hot_interaction_retries_when_persist_fails(tmp_path) -> None:
    config = VingolfConfig(
        review=ReviewConfig(
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=1,
        )
    )
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["ok"] * 10),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    agent = await app.create_agent("Dora", "参与者")
    topic = await app.create_topic(title="重试测试", description="desc", agents=[agent])
    assert app.topic is not None
    post = await app.topic.add_agent_post(topic.id, agent, "值得重试的内容")

    assert app.review is not None
    app.review._review_post = AsyncMock(return_value=_make_hot_review_result(post))  # type: ignore[method-assign]
    app.review._persist_review_result = AsyncMock(side_effect=[False, True])  # type: ignore[method-assign]

    await app.like_post(topic.id, post.id)
    await app.like_post(topic.id, post.id)

    assert app.review._review_post.await_count == 2  # type: ignore[attr-defined]
    assert app.review._persist_review_result.await_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_hot_interaction_failure_reason_is_persisted(tmp_path) -> None:
    config = VingolfConfig(
        review=ReviewConfig(
            use_team=True,
            hot_interaction_enabled=True,
            hot_interaction_likes_threshold=1,
        ),
        db_path=str(tmp_path / "vingolf-failure.db"),
    )
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["ok"] * 10),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    await app.startup()
    try:
        agent = await app.create_agent("FailCase", "参与者")
        topic = await app.create_topic(title="失败原因", description="desc", agents=[agent])
        assert app.topic is not None
        post = await app.topic.add_agent_post(topic.id, agent, "会失败的帖子")

        assert app.review is not None
        assert app.review._review_team is not None
        with patch.object(
            app.review._review_team,
            "review_post_in_topic",
            new=AsyncMock(side_effect=RuntimeError("ThreadReviewer exploded")),
        ):
            await app.like_post(topic.id, post.id)

        assert app._review_repo is not None
        records = await app._review_repo.get_review_records_for_topic(topic.id)
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert "RuntimeError: ThreadReviewer exploded" == records[0]["error_message"]
        assert records[0]["strategy_results"] == []
        assert records[0]["growth_directives"][0]["kind"] == "no_action"
    finally:
        await app.shutdown()


# ---------------------------------------------------------------------------
# ConsolidationService
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidation_applies_memory_candidate(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="memory_candidate", priority="high")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        # Register the agent so it exists in the registry
        agent_profile = AgentProfile(id="a1", name="Alice", description="")
        agent = idavoll_app.agents.register(agent_profile)

        # Give agent a mock memory
        mock_memory = MagicMock()
        mock_memory.write_fact = MagicMock(return_value=True)
        agent.memory = mock_memory

        service = ConsolidationService(idavoll_app, repo)
        service._reflect_and_decide = AsyncMock(  # type: ignore[method-assign]
            return_value=ConsolidationDecision(
                decision="accept",
                content="Alice 应保持清晰表达",
                rationale="这条反馈准确且值得长期保留。",
            )
        )
        count = await service.consolidate("a1")

        assert count == 1
        mock_memory.write_fact.assert_called_once()
        call_kwargs = mock_memory.write_fact.call_args
        assert "Alice 应保持清晰表达" in str(call_kwargs)

        # Directive should now be applied
        remaining = await repo.get_pending_directives("a1")
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_consolidation_no_action_marks_applied(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="no_action", priority="low")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        agent_profile = AgentProfile(id="a1", name="Alice", description="")
        idavoll_app.agents.register(agent_profile)

        service = ConsolidationService(idavoll_app, repo)
        count = await service.consolidate("a1")

        assert count == 1
        remaining = await repo.get_pending_directives("a1")
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_consolidation_skips_missing_agent(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(agent_id="ghost-agent", kind="memory_candidate", priority="high")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        # No agent registered — service should not crash
        service = ConsolidationService(idavoll_app, repo)
        count = await service.consolidate("ghost-agent")

        # Should still mark applied (no write, but completes gracefully)
        assert count == 1


@pytest.mark.asyncio
async def test_consolidation_all_multiple_agents(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        await repo.save_review(_make_record(agent_id="a1", kind="no_action", priority="low"))
        await repo.save_review(_make_record(agent_id="a2", kind="no_action", priority="low", topic_id="t2"))

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        for aid, name in [("a1", "Alice"), ("a2", "Bob")]:
            idavoll_app.agents.register(AgentProfile(id=aid, name=name, description=""))

        service = ConsolidationService(idavoll_app, repo)
        result = await service.consolidate_all()

        assert "a1" in result
        assert "a2" in result
        assert result["a1"] == 1
        assert result["a2"] == 1


@pytest.mark.asyncio
async def test_consolidation_reflection_emits_hook(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="reflection_candidate", priority="medium")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        idavoll_app.agents.register(AgentProfile(id="a1", name="Alice", description=""))

        emitted = []

        @idavoll_app.hooks.hook("review.reflection_ready")
        async def capture(**kwargs):
            emitted.append(kwargs)

        service = ConsolidationService(idavoll_app, repo)
        service._reflect_and_decide = AsyncMock(  # type: ignore[method-assign]
            return_value=ConsolidationDecision(
                decision="accept",
                content="以后要更系统地复盘自己的论证深度。",
                rationale="这条反馈适合作为后续自我反思的起点。",
            )
        )
        count = await service.consolidate("a1")

        assert count == 1
        assert len(emitted) == 1
        assert emitted[0]["agent_id"] == "a1"
        assert "以后要更系统地复盘" in emitted[0]["content"]


@pytest.mark.asyncio
async def test_consolidation_rejects_memory_candidate_without_write(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="memory_candidate", priority="high")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        agent_profile = AgentProfile(id="a1", name="Alice", description="")
        agent = idavoll_app.agents.register(agent_profile)
        mock_memory = MagicMock()
        mock_memory.write_fact = MagicMock(return_value=True)
        agent.memory = mock_memory

        service = ConsolidationService(idavoll_app, repo)
        service._reflect_and_decide = AsyncMock(  # type: ignore[method-assign]
            return_value=ConsolidationDecision(
                decision="reject",
                content="",
                rationale="这条建议过于泛化，不适合直接固化到长期记忆。",
            )
        )
        count = await service.consolidate("a1")

        assert count == 1
        mock_memory.write_fact.assert_not_called()
        remaining = await repo.get_pending_directives("a1")
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_consolidation_defer_keeps_directive_pending(tmp_path) -> None:
    async with Database(tmp_path / "test.db") as db:
        repo = ReviewRepository(db)
        record = _make_record(kind="memory_candidate", priority="high")
        await repo.save_review(record)

        idavoll_app = IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        )
        agent_profile = AgentProfile(id="a1", name="Alice", description="")
        agent = idavoll_app.agents.register(agent_profile)
        mock_memory = MagicMock()
        mock_memory.write_fact = MagicMock(return_value=True)
        agent.memory = mock_memory

        service = ConsolidationService(idavoll_app, repo)
        service._reflect_and_decide = AsyncMock(  # type: ignore[method-assign]
            return_value=ConsolidationDecision(
                decision="defer",
                content="",
                rationale="还需要更多上下文和重复证据。",
            )
        )
        count = await service.consolidate("a1")

        assert count == 0
        mock_memory.write_fact.assert_not_called()
        remaining = await repo.get_pending_directives("a1")
        assert len(remaining) == 1


def test_consolidation_prompt_includes_review_evidence() -> None:
    directive = {
        "id": 7,
        "review_id": "r1",
        "kind": "memory_candidate",
        "priority": "high",
        "content": "保持清晰表达",
        "rationale": "多位 reviewer 认为论证结构清晰。",
        "review_created_at": "2026-04-16T10:00:00+00:00",
    }
    review_record = {
        "trigger_type": "topic_closed",
        "target_type": "agent_in_topic",
        "target_id": "agent-1",
        "quality_score": 8.2,
        "confidence": 0.81,
        "summary": "整体表现稳定",
        "strategy_results": [
            {
                "reviewer_name": "DepthReviewer",
                "dimension": "depth",
                "score": 8.5,
                "confidence": 0.9,
                "summary": "论证结构清晰",
                "evidence": ["先提出立场，再给出推理链", "回应了反方观点"],
                "concerns": ["结尾略短"],
            }
        ],
    }

    prompt = ConsolidationService._build_reflection_prompt(
        directive=directive,
        review_record=review_record,
    )

    assert "【Review 上下文】" in prompt
    assert "Quality Score: 8.2" in prompt
    assert "DepthReviewer" in prompt
    assert "先提出立场，再给出推理链" in prompt
    assert "结尾略短" in prompt


# ---------------------------------------------------------------------------
# VingolfApp.startup wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_wires_review_repo(tmp_path) -> None:
    config = VingolfConfig(review=ReviewConfig())
    app = VingolfApp(
        IdavollApp(
            llm=FakeLLM(replies=["ok"]),
            config=IdavollConfig(workspace={"base_dir": tmp_path / "ws"}),
        ),
        config=config,
    )
    await app.startup()
    try:
        assert app._review_repo is not None
        assert app.consolidation is not None
        assert app.review is not None
        assert app.review._repo is app._review_repo
    finally:
        await app.shutdown()


def test_vingolf_config_loads_separate_review_plan_yaml(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    plan_path = tmp_path / "review_plan.yaml"
    config_path.write_text(
        """
vingolf:
  review:
    use_team: true
""".strip(),
        encoding="utf-8",
    )
    plan_path.write_text(
        """
vingolf:
  review_plan:
    default_roles_for_post:
      - DepthReviewer
      - ThreadReviewer
""".strip(),
        encoding="utf-8",
    )

    config = VingolfConfig.from_yaml(config_path)

    assert config.review.use_team is True
    assert config.review_plan.default_roles_for_post == [
        "DepthReviewer",
        "ThreadReviewer",
    ]

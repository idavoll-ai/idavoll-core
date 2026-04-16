from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from idavoll.plugin.base import IdavollPlugin
from vingolf.config import ReviewConfig, ReviewPlanConfig
from .review_team import GrowthDirective, ReviewerOutput
from .topic import Post, Topic

if TYPE_CHECKING:
    from idavoll.app import IdavollApp
    from vingolf.persistence.review_repo import ReviewRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM review prompts
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """\
你是一个内容质量评审员，对 Agent 在话题讨论中的发言进行多维度评分。

评分维度（每维度 1–10 分）：
- relevance:    相关性 — 发言与话题主旨的贴合程度
- depth:        深度   — 观点的洞察力和论证质量
- originality:  独创性 — 视角的新颖程度
- engagement:   互动性 — 与其他发言的互动和引用

只返回 JSON，不含其他文字：
{"relevance": 8, "depth": 7, "originality": 6, "engagement": 5, "comment": "简短评语（不超过80字,不少于30字）"}\
"""

_REVIEW_USER_TMPL = """\
话题标题：{title}
话题描述：{description}

---- 完整讨论摘要 ----
{all_posts}

---- 待评审 Agent ----
名称：{agent_name}
发言内容：
{agent_posts}\
"""

_DIMENSION_KEYS = ("relevance", "depth", "originality", "engagement")


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class DimensionScores(BaseModel):
    """Multi-dimensional LLM evaluation scores (each 1–10)."""

    relevance: float = 5.0
    depth: float = 5.0
    originality: float = 5.0
    engagement: float = 5.0

    @property
    def average(self) -> float:
        return (self.relevance + self.depth + self.originality + self.engagement) / 4


class AgentReviewResult(BaseModel):
    agent_id: str
    agent_name: str
    target_type: Literal["agent_in_topic", "post", "thread"] = "agent_in_topic"
    target_id: str | None = None
    post_count: int
    likes_count: int
    # content quality score (LLM composite or deterministic fallback, 1–10)
    composite_score: float
    # community engagement score (normalised likes, 1–10)
    likes_score: float
    # weighted blend of the two
    final_score: float
    # per-dimension breakdown — empty when LLM scoring was skipped / failed
    dimensions: DimensionScores = Field(default_factory=DimensionScores)
    # reviewer comment — from LLM or generic fallback
    summary: str
    # moderator confidence (0–1); 1.0 when using the legacy single-LLM path
    confidence: float = 1.0
    growth_priority: Literal["low", "medium", "high"] = "low"
    review_status: Literal["completed", "failed"] = "completed"
    error_message: str | None = None
    # evidence snippets collected by reviewer subagents
    evidence: list[str] = Field(default_factory=list)
    key_strengths: list[str] = Field(default_factory=list)
    key_weaknesses: list[str] = Field(default_factory=list)
    # structured growth signal produced by the review pipeline (never raw writes to MEMORY.md)
    growth_directives: list[GrowthDirective] = Field(default_factory=list)
    reviewer_outputs: list[ReviewerOutput] = Field(default_factory=list)


class TopicReviewSummary(BaseModel):
    topic_id: str
    topic_title: str
    results: list[AgentReviewResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ReviewPlugin
# ---------------------------------------------------------------------------

class ReviewPlugin(IdavollPlugin):
    """Review pipeline that evaluates agent contributions in a topic.

    Two execution paths:
    - use_team=False (default): single LLM call, legacy behaviour.
    - use_team=True  (Phase 2): parallel reviewer subagents + Moderator.

    When the LLM call or subagent execution fails the plugin silently falls
    back to deterministic post-count scoring so reviews always complete.
    """

    name = "vingolf.review"

    def __init__(
        self,
        config: ReviewConfig | None = None,
        plan_config: ReviewPlanConfig | None = None,
    ) -> None:
        self._config = config or ReviewConfig()
        self._plan_config = plan_config or ReviewPlanConfig()
        self._app: IdavollApp | None = None
        self._summaries: dict[str, TopicReviewSummary] = {}
        self._review_team: "ReviewTeam | None" = None
        self._repo: "ReviewRepository | None" = None
        self._topic_plugin: "TopicPlugin | None" = None
        # Tracks post IDs already handled by HotInteractionTrigger (prevents duplicates)
        self._hot_reviewed_posts: set[str] = set()

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        # Capture TopicPlugin reference if already installed (it precedes ReviewPlugin
        # in VingolfApp's default plugin order, so this is always set).
        from .topic import TopicPlugin as _TopicPlugin
        self._topic_plugin = next(
            (p for p in app._plugins if isinstance(p, _TopicPlugin)),
            None,
        )

        if self._config.use_team:
            from .review_team import ReviewTeam
            from idavoll.agent.profile import AgentProfile
            from idavoll.agent.registry import Agent as _Agent
            orchestrator = _Agent(
                profile=AgentProfile(name="ReviewOrchestrator"),
                metadata={"runtime_mode": "orchestrator", "delegate_depth": 0},
            )
            self._review_team = ReviewTeam(
                app,
                orchestrator,
                self._config,
                self._plan_config,
            )

        @app.hooks.hook("topic.closed")
        async def on_topic_closed(topic: Topic, posts: list[Post], **_) -> None:
            summary = await self._summarize(topic, posts)
            self._summaries[topic.id] = summary
            await app.hooks.emit("review.completed", summary=summary)

        if self._config.hot_interaction_enabled:
            @app.hooks.hook("topic.post.liked")
            async def on_post_liked(topic: Topic, post: Post, **_) -> None:
                await self._handle_hot_interaction(topic, post)

    def get_summary(self, topic_id: str) -> TopicReviewSummary | None:
        return self._summaries.get(topic_id)

    def clear_summary(self, topic_id: str) -> None:
        self._summaries.pop(topic_id, None)

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def _summarize(self, topic: Topic, posts: list[Post]) -> TopicReviewSummary:
        by_agent: dict[str, list[Post]] = {}
        for post in posts:
            if post.source != "agent":
                continue
            by_agent.setdefault(post.author_id, []).append(post)

        max_likes = max(
            (sum(p.likes for p in group) for group in by_agent.values()), default=0
        )
        all_posts_text = self._format_all_posts(posts)

        results: list[AgentReviewResult] = []
        for agent_id, group in by_agent.items():
            result = await self._review_agent(
                topic=topic,
                agent_id=agent_id,
                agent_name=group[0].author_name,
                agent_posts=group,
                all_posts_text=all_posts_text,
                max_likes=max_likes,
            )
            results.append(result)
            await self._persist_review_result(
                result,
                topic_id=topic.id,
                session_id=topic.session_id,
            )

        return TopicReviewSummary(
            topic_id=topic.id,
            topic_title=topic.title,
            results=results,
        )

    async def _review_agent(
        self,
        *,
        topic: Topic,
        agent_id: str,
        agent_name: str,
        agent_posts: list[Post],
        all_posts_text: str,
        max_likes: int,
    ) -> AgentReviewResult:
        post_count = len(agent_posts)
        likes_count = sum(p.likes for p in agent_posts)
        likes_score = self._normalize_likes(likes_count, max_likes)

        # --- Team path (Phase 2) ---
        if self._config.use_team and self._review_team is not None:
            return await self._team_review(
                topic=topic,
                agent_id=agent_id,
                agent_name=agent_name,
                agent_posts=agent_posts,
                all_posts_text=all_posts_text,
                post_count=post_count,
                likes_count=likes_count,
                likes_score=likes_score,
            )

        # --- Legacy single-LLM path ---
        dimensions = DimensionScores()
        comment: str | None = None

        if self._config.use_llm and self._app is not None:
            dimensions, comment = await self._llm_review(
                topic=topic,
                agent_name=agent_name,
                agent_posts=agent_posts,
                all_posts_text=all_posts_text,
            )

        composite_score = dimensions.average if comment is not None else self._fallback_score(post_count)
        final_score = round(
            composite_score * self._config.composite_weight
            + likes_score * self._config.likes_weight,
            2,
        )

        directive = self._make_directive(
            final_score=final_score,
            comment=comment,
            topic_title=topic.title,
        )
        return AgentReviewResult(
            agent_id=agent_id,
            agent_name=agent_name,
            target_type="agent_in_topic",
            target_id=agent_id,
            post_count=post_count,
            likes_count=likes_count,
            composite_score=round(composite_score, 2),
            likes_score=round(likes_score, 2),
            final_score=final_score,
            dimensions=dimensions,
            summary=comment or f"共发言 {post_count} 次，累计获得 {likes_count} 个点赞。",
            growth_priority=self._growth_priority_for_score(final_score),
            growth_directives=[directive],
        )

    # ------------------------------------------------------------------
    # Team evaluation (Phase 2)
    # ------------------------------------------------------------------

    async def _team_review(
        self,
        *,
        topic: Topic,
        agent_id: str,
        agent_name: str,
        agent_posts: list[Post],
        all_posts_text: str,
        post_count: int,
        likes_count: int,
        likes_score: float,
    ) -> AgentReviewResult:
        assert self._review_team is not None
        try:
            outcome, reviewer_outputs = await self._review_team.review_agent_in_topic(
                topic=topic,
                agent_name=agent_name,
                agent_id=agent_id,
                agent_posts=agent_posts,
                all_posts_text=all_posts_text,
            )
        except Exception as exc:
            logger.warning(
                "ReviewTeam failed for %r in topic %r; falling back to deterministic",
                agent_name, topic.id, exc_info=True,
            )
            composite_score = self._fallback_score(post_count)
            final_score = round(
                composite_score * self._config.composite_weight
                + likes_score * self._config.likes_weight,
                2,
            )
            return AgentReviewResult(
                agent_id=agent_id,
                agent_name=agent_name,
                target_type="agent_in_topic",
                target_id=agent_id,
                post_count=post_count,
                likes_count=likes_count,
                composite_score=round(composite_score, 2),
                likes_score=round(likes_score, 2),
                final_score=final_score,
                summary=f"共发言 {post_count} 次，累计获得 {likes_count} 个点赞。",
                confidence=0.0,
                growth_priority=self._growth_priority_for_score(final_score),
                review_status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
                growth_directives=[GrowthDirective(
                    kind="no_action",
                    priority="low",
                    content="",
                    rationale="ReviewTeam 执行失败，降级为 no_action。",
                )],
            )

        # Blend team quality_score with likes_score for final_score.
        composite_score = outcome.quality_score
        final_score = round(
            composite_score * self._config.composite_weight
            + likes_score * self._config.likes_weight,
            2,
        )
        all_evidence = [e for ro in reviewer_outputs for e in ro.evidence]

        return AgentReviewResult(
            agent_id=agent_id,
            agent_name=agent_name,
            target_type="agent_in_topic",
            target_id=agent_id,
            post_count=post_count,
            likes_count=likes_count,
            composite_score=round(composite_score, 2),
            likes_score=round(likes_score, 2),
            final_score=final_score,
            dimensions=DimensionScores(
                depth=next(
                    (o.score for o in reviewer_outputs if o.dimension == "depth"), 5.0
                ),
                engagement=next(
                    (o.score for o in reviewer_outputs if o.dimension == "engagement"), 5.0
                ),
                relevance=outcome.quality_score,
                originality=outcome.quality_score,
            ),
            summary=outcome.summary or f"共发言 {post_count} 次，累计获得 {likes_count} 个点赞。",
            confidence=outcome.confidence,
            growth_priority=outcome.growth_priority,
            review_status="completed",
            evidence=all_evidence[:8],
            key_strengths=list(outcome.key_strengths),
            key_weaknesses=list(outcome.key_weaknesses),
            growth_directives=outcome.growth_directives,
            reviewer_outputs=list(reviewer_outputs),
        )

    async def _review_post(
        self,
        *,
        topic: Topic,
        post: Post,
        context_posts: list[Post],
        max_likes: int,
    ) -> AgentReviewResult:
        post_count = 1
        likes_count = post.likes
        likes_score = self._normalize_likes(likes_count, max_likes)
        all_posts_text = self._format_all_posts(context_posts)

        if self._config.use_team and self._review_team is not None:
            try:
                outcome, reviewer_outputs = await self._review_team.review_post_in_topic(
                    topic=topic,
                    post=post,
                    context_posts=context_posts,
                )
            except Exception as exc:
                logger.warning(
                    "ReviewTeam failed for hot post %r in topic %r; falling back to deterministic",
                    post.id,
                    topic.id,
                    exc_info=True,
                )
                composite_score = self._fallback_score(post_count)
                final_score = round(
                    composite_score * self._config.composite_weight
                    + likes_score * self._config.likes_weight,
                    2,
                )
                return AgentReviewResult(
                    agent_id=post.author_id,
                    agent_name=post.author_name,
                    target_type="post",
                    target_id=post.id,
                    post_count=post_count,
                    likes_count=likes_count,
                    composite_score=round(composite_score, 2),
                    likes_score=round(likes_score, 2),
                    final_score=final_score,
                    summary=f"帖子获得 {likes_count} 个点赞。",
                    confidence=0.0,
                    growth_priority=self._growth_priority_for_score(final_score),
                    review_status="failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                    growth_directives=[GrowthDirective(
                        kind="no_action",
                        priority="low",
                        content="",
                        rationale="HotInteraction ReviewTeam 执行失败，降级为 no_action。",
                    )],
                )

            composite_score = outcome.quality_score
            final_score = round(
                composite_score * self._config.composite_weight
                + likes_score * self._config.likes_weight,
                2,
            )
            all_evidence = [e for ro in reviewer_outputs for e in ro.evidence]
            return AgentReviewResult(
                agent_id=post.author_id,
                agent_name=post.author_name,
                target_type="post",
                target_id=post.id,
                post_count=post_count,
                likes_count=likes_count,
                composite_score=round(composite_score, 2),
                likes_score=round(likes_score, 2),
                final_score=final_score,
                dimensions=DimensionScores(
                    depth=next(
                        (o.score for o in reviewer_outputs if o.dimension == "depth"), 5.0
                    ),
                    engagement=next(
                        (o.score for o in reviewer_outputs if o.dimension == "engagement"), 5.0
                    ),
                    relevance=outcome.quality_score,
                    originality=outcome.quality_score,
                ),
                summary=outcome.summary or f"帖子获得 {likes_count} 个点赞。",
                confidence=outcome.confidence,
                growth_priority=outcome.growth_priority,
                review_status="completed",
                evidence=all_evidence[:8],
                key_strengths=list(outcome.key_strengths),
                key_weaknesses=list(outcome.key_weaknesses),
                growth_directives=outcome.growth_directives,
                reviewer_outputs=list(reviewer_outputs),
            )

        dimensions = DimensionScores()
        comment: str | None = None

        if self._config.use_llm and self._app is not None:
            dimensions, comment = await self._llm_review(
                topic=topic,
                agent_name=post.author_name,
                agent_posts=[post],
                all_posts_text=all_posts_text,
            )

        composite_score = (
            dimensions.average if comment is not None else self._fallback_score(post_count)
        )
        final_score = round(
            composite_score * self._config.composite_weight
            + likes_score * self._config.likes_weight,
            2,
        )
        directive = self._make_directive(
            final_score=final_score,
            comment=comment,
            topic_title=topic.title,
        )
        return AgentReviewResult(
            agent_id=post.author_id,
            agent_name=post.author_name,
            target_type="post",
            target_id=post.id,
            post_count=post_count,
            likes_count=likes_count,
            composite_score=round(composite_score, 2),
            likes_score=round(likes_score, 2),
            final_score=final_score,
            dimensions=dimensions,
            summary=comment or f"帖子获得 {likes_count} 个点赞。",
            growth_priority=self._growth_priority_for_score(final_score),
            review_status="completed",
            growth_directives=[directive],
        )

    # ------------------------------------------------------------------
    # Hot interaction trigger (Phase 3)
    # ------------------------------------------------------------------

    async def _handle_hot_interaction(self, topic: Topic, post: Post) -> None:
        """Called when a post receives a like. Triggers a focused review when
        the like count reaches the configured threshold and the post has not
        already been hot-reviewed.
        """
        if post.id in self._hot_reviewed_posts:
            return
        if post.likes < self._config.hot_interaction_likes_threshold:
            return
        if post.source != "agent":
            return

        logger.info(
            "HotInteractionTrigger: post %r by %r reached %d likes in topic %r",
            post.id, post.author_name, post.likes, topic.id,
        )

        # Collect all posts in the topic to build context.
        topic_posts = []
        if self._topic_plugin is not None:
            topic_posts = self._topic_plugin.get_posts(topic.id)

        if not topic_posts:
            topic_posts = [post]

        context_posts = self._collect_thread_posts(post, topic_posts)
        max_likes = max((p.likes for p in topic_posts), default=post.likes)

        try:
            result = await self._review_post(
                topic=topic,
                post=post,
                context_posts=context_posts,
                max_likes=max_likes,
            )
            persisted = await self._persist_review_result(
                result,
                topic_id=topic.id,
                session_id=topic.session_id,
                trigger_type="hot_interaction",
            )
            if persisted:
                self._hot_reviewed_posts.add(post.id)
        except Exception:
            logger.warning(
                "HotInteractionTrigger: review failed for post %r", post.id, exc_info=True
            )

    async def _persist_review_result(
        self,
        result: AgentReviewResult,
        *,
        topic_id: str,
        session_id: str | None = None,
        trigger_type: str = "topic_closed",
    ) -> bool:
        """Save an AgentReviewResult to the ReviewRepository as a ReviewRecord."""
        if self._repo is None:
            return True
        from .review_team import ReviewOutcome, ReviewRecord
        outcome = ReviewOutcome(
            quality_score=result.composite_score,
            confidence=result.confidence,
            summary=result.summary,
            key_strengths=list(result.key_strengths) or [e for e in result.evidence],
            key_weaknesses=list(result.key_weaknesses),
            growth_priority=result.growth_priority,
            growth_directives=list(result.growth_directives),
        )
        record = ReviewRecord(
            topic_id=topic_id,
            session_id=session_id,
            agent_id=result.agent_id,
            agent_name=result.agent_name,
            target_type=result.target_type,
            target_id=result.target_id,
            status=result.review_status,
            error_message=result.error_message,
            reviewer_outputs=list(result.reviewer_outputs),
            outcome=outcome,
        )
        try:
            await self._repo.save_review(record, trigger_type=trigger_type)
            return True
        except Exception:
            logger.warning(
                "ReviewPlugin: failed to persist review record for agent %r",
                result.agent_id, exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # LLM evaluation
    # ------------------------------------------------------------------

    async def _llm_review(
        self,
        *,
        topic: Topic,
        agent_name: str,
        agent_posts: list[Post],
        all_posts_text: str,
    ) -> tuple[DimensionScores, str | None]:
        """Call the LLM to evaluate this agent's contribution.

        Returns ``(DimensionScores, comment)`` on success, or
        ``(DimensionScores(all 5.0), None)`` on any failure so the caller
        can detect and fall back to deterministic scoring.
        """
        assert self._app is not None

        agent_posts_text = self._format_agent_posts(agent_posts)
        prompt = _REVIEW_USER_TMPL.format(
            title=topic.title,
            description=topic.description,
            all_posts=all_posts_text,
            agent_name=agent_name,
            agent_posts=agent_posts_text,
        )

        try:
            raw = await self._app.llm.generate(
                [
                    SystemMessage(content=_REVIEW_SYSTEM),
                    HumanMessage(content=prompt),
                ],
                run_name="review-agent",
                metadata={"topic_id": topic.id, "agent_name": agent_name},
            )
            return self._parse_review(raw)
        except Exception:
            logger.warning(
                "ReviewPlugin: LLM call failed for agent %r in topic %r; "
                "falling back to deterministic scoring.",
                agent_name,
                topic.id,
                exc_info=True,
            )
            return DimensionScores(), None

    # ------------------------------------------------------------------
    # Directive generation
    # ------------------------------------------------------------------

    def _make_directive(
        self,
        *,
        final_score: float,
        comment: str | None,
        topic_title: str,
    ) -> GrowthDirective:
        """Produce a GrowthDirective from a single-reviewer result.

        Phase 0 heuristic (pre review-team):
        - No LLM comment → no_action (insufficient confidence)
        - score >= min_score_for_memory_candidate → memory_candidate
        - score < threshold → reflection_candidate
        """
        if comment is None:
            return GrowthDirective(
                kind="no_action",
                priority="low",
                content="",
                rationale="LLM 评分未成功，无可靠反馈。",
            )
        threshold = self._config.min_score_for_memory_candidate
        context = f"「{topic_title}」" if topic_title else "话题"
        if final_score >= threshold:
            priority = "high" if final_score >= 8.5 else "medium"
            return GrowthDirective(
                kind="memory_candidate",
                priority=priority,
                content=comment,
                rationale=f"{context}综合得分 {final_score:.1f}/10，达到记忆候选阈值（{threshold}）。",
                ttl_days=30,
            )
        priority = "medium" if final_score >= 5.0 else "high"
        return GrowthDirective(
            kind="reflection_candidate",
            priority=priority,
            content=comment,
            rationale=f"{context}综合得分 {final_score:.1f}/10，未达记忆阈值，建议反思改进。",
            ttl_days=14,
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_all_posts(self, posts: list[Post]) -> str:
        """Render all posts as a compact discussion transcript."""
        lines: list[str] = []
        for post in posts:
            prefix = f"[{post.author_name}]"
            if post.reply_to:
                prefix += " (回复)"
            # Truncate very long posts to stay within token budget
            content = post.content[: self._config.max_post_chars]
            lines.append(f"{prefix} {content}")
        return "\n".join(lines)

    def _format_agent_posts(self, posts: list[Post]) -> str:
        lines: list[str] = []
        for i, post in enumerate(posts, 1):
            content = post.content[: self._config.max_post_chars]
            lines.append(f"{i}. {content}")
        return "\n".join(lines)

    @staticmethod
    def _collect_thread_posts(target_post: Post, posts: list[Post]) -> list[Post]:
        posts_by_id = {post.id: post for post in posts}
        thread_ids: set[str] = {target_post.id}

        current = target_post
        while current.reply_to is not None:
            parent = posts_by_id.get(current.reply_to)
            if parent is None or parent.id in thread_ids:
                break
            thread_ids.add(parent.id)
            current = parent

        pending = [target_post.id]
        while pending:
            parent_id = pending.pop(0)
            for post in posts:
                if post.reply_to != parent_id or post.id in thread_ids:
                    continue
                thread_ids.add(post.id)
                pending.append(post.id)

        return [post for post in posts if post.id in thread_ids]

    # ------------------------------------------------------------------
    # Parsing + scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_review(raw: str) -> tuple[DimensionScores, str | None]:
        """Parse LLM JSON response into (DimensionScores, comment).

        Returns neutral scores + None on any parse failure so callers can
        distinguish "LLM answered" from "LLM failed".
        """
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        start = cleaned.find("{")
        if start > 0:
            cleaned = cleaned[start:]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return DimensionScores(), None

        if not isinstance(data, dict):
            return DimensionScores(), None

        def _clamp(key: str) -> float:
            val = data.get(key, 5.0)
            try:
                return max(1.0, min(10.0, float(val)))
            except (TypeError, ValueError):
                return 5.0

        scores = DimensionScores(
            relevance=_clamp("relevance"),
            depth=_clamp("depth"),
            originality=_clamp("originality"),
            engagement=_clamp("engagement"),
        )
        comment = str(data.get("comment", "")).strip() or None
        return scores, comment

    @staticmethod
    def _fallback_score(post_count: int) -> float:
        """Deterministic content score when LLM is disabled or fails."""
        return min(10.0, 5.0 + post_count * 0.8)

    @staticmethod
    def _growth_priority_for_score(score: float) -> Literal["low", "medium", "high"]:
        if score >= 8.0:
            return "high"
        if score >= 6.0:
            return "medium"
        return "low"

    @staticmethod
    def _normalize_likes(total_likes: int, max_likes: int) -> float:
        if max_likes <= 0:
            return 5.0
        return 1.0 + (total_likes / max_likes) * 9.0

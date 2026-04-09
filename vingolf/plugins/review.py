from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from idavoll.plugin.base import IdavollPlugin
from vingolf.config import ReviewConfig
from .topic import Post, Topic

if TYPE_CHECKING:
    from idavoll.app import IdavollApp

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
{"relevance": 8, "depth": 7, "originality": 6, "engagement": 5, "comment": "简短评语（不超过80字）"}\
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


class TopicReviewSummary(BaseModel):
    topic_id: str
    topic_title: str
    results: list[AgentReviewResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ReviewPlugin
# ---------------------------------------------------------------------------

class ReviewPlugin(IdavollPlugin):
    """Review pipeline that uses the LLM for multi-dimensional content scoring.

    Architecture note (§4.3 mvp_design.md)
    ----------------------------------------
    No Idavoll Core changes are required.  ``install(app)`` receives an
    ``IdavollApp`` reference; ``app.llm`` (LLMAdapter) is used directly to
    evaluate each agent's posts when ``config.use_llm=True``.

    When the LLM call fails (bad JSON, network error, etc.) the plugin
    silently falls back to deterministic post-count scoring so reviews
    always complete even in degraded environments.
    """

    name = "vingolf.review"

    def __init__(self, config: ReviewConfig | None = None) -> None:
        self._config = config or ReviewConfig()
        self._app: IdavollApp | None = None
        self._summaries: dict[str, TopicReviewSummary] = {}

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        @app.hooks.hook("topic.closed")
        async def on_topic_closed(topic: Topic, posts: list[Post], **_) -> None:
            summary = await self._summarize(topic, posts)
            self._summaries[topic.id] = summary
            await app.hooks.emit("review.completed", summary=summary)

    def get_summary(self, topic_id: str) -> TopicReviewSummary | None:
        return self._summaries.get(topic_id)

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

        return AgentReviewResult(
            agent_id=agent_id,
            agent_name=agent_name,
            post_count=post_count,
            likes_count=likes_count,
            composite_score=round(composite_score, 2),
            likes_score=round(likes_score, 2),
            final_score=final_score,
            dimensions=dimensions,
            summary=comment or f"共发言 {post_count} 次，累计获得 {likes_count} 个点赞。",
        )

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
    def _normalize_likes(total_likes: int, max_likes: int) -> float:
        if max_likes <= 0:
            return 5.0
        return 1.0 + (total_likes / max_likes) * 9.0

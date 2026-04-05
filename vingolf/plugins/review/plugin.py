from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from idavoll.plugin.base import IdavollPlugin

from vingolf.config import ReviewConfig
from ..topic.models import Post, Topic
from .models import AgentReviewResult, DimensionScore, TopicReviewSummary
from . import reviewers

if TYPE_CHECKING:
    from idavoll.app import IdavollApp


class ReviewPlugin(IdavollPlugin):
    """
    Multi-agent review panel that scores agents after a topic closes.

    Pipeline (per agent in the topic)
    ----------------------------------
    1. Collect all posts by that agent.
    2. Three reviewers independently score in parallel:
         Logic Reviewer    → logic score (1-10)
         Creativity Reviewer → creativity score (1-10)
         Social Reviewer   → social score (1-10)
    3. Panel Moderator sees all three reviews and produces negotiated scores.
    4. Compute final_score = composite * 0.5 + likes_score * 0.5
    5. Write score back to each Post and emit `vingolf.review.completed`.

    Trigger
    -------
    Listens for `vingolf.topic.review_requested` emitted by TopicPlugin.
    """

    name = "vingolf.review"

    def __init__(
        self,
        config: ReviewConfig | None = None,
        *,
        max_post_chars: int | None = None,
    ) -> None:
        """
        Args:
            config: A :class:`~vingolf.config.ReviewConfig` instance.
                    When omitted, defaults apply.
            max_post_chars: Shorthand override for ``config.max_post_chars``.
        """
        self._app: IdavollApp | None = None
        self._config = config or ReviewConfig()
        if max_post_chars is not None:
            self._config = self._config.model_copy(update={"max_post_chars": max_post_chars})
        self._summaries: dict[str, TopicReviewSummary] = {}  # topic_id → summary

    # ── IdavollPlugin interface ───────────────────────────────────────────────

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        @app.hooks.hook("vingolf.topic.review_requested")
        async def on_review_requested(topic: Topic, posts: list[Post], **_) -> None:
            summary = await self._run_review(topic, posts)
            self._summaries[topic.id] = summary
            await app.hooks.emit("vingolf.review.completed", summary=summary)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_summary(self, topic_id: str) -> TopicReviewSummary | None:
        return self._summaries.get(topic_id)

    # ── Review pipeline ───────────────────────────────────────────────────────

    async def _run_review(self, topic: Topic, posts: list[Post]) -> TopicReviewSummary:
        app = self._require_app()
        llm = app.llm.raw

        # Group posts by agent
        by_agent: dict[str, list[Post]] = {}
        for post in posts:
            by_agent.setdefault(post.agent_id, []).append(post)

        # Compute max likes across all agents for normalization
        max_likes = max(
            (sum(p.likes for p in agent_posts) for agent_posts in by_agent.values()),
            default=0,
        )

        # Review each agent in parallel
        results = await asyncio.gather(
            *[
                self._review_agent(
                    llm=llm,
                    agent_id=agent_id,
                    agent_name=agent_posts[0].agent_name,
                    posts=agent_posts,
                    max_likes=max_likes,
                )
                for agent_id, agent_posts in by_agent.items()
            ]
        )

        # Write score back to each Post object
        score_map = {r.agent_id: r.final_score for r in results}
        for post in posts:
            post.score = score_map.get(post.agent_id)

        return TopicReviewSummary(
            topic_id=topic.id,
            topic_title=topic.title,
            results=list(results),
        )

    async def _review_agent(
        self,
        llm,
        agent_id: str,
        agent_name: str,
        posts: list[Post],
        max_likes: int,
    ) -> AgentReviewResult:
        posts_text = self._format_posts(posts)
        post_count = len(posts)

        # Phase 1 — independent scoring (parallel)
        logic, creativity, social = await asyncio.gather(
            reviewers.score_logic(llm, agent_name, posts_text, post_count),
            reviewers.score_creativity(llm, agent_name, posts_text, post_count),
            reviewers.score_social(llm, agent_name, posts_text, post_count),
        )

        # Phase 2 — negotiation
        negotiated = await reviewers.negotiate(llm, agent_name, logic, creativity, social)

        # Phase 3 — final score calculation
        composite = (
            negotiated.logic_score
            + negotiated.creativity_score
            + negotiated.social_score
        ) / 3.0

        total_likes = sum(p.likes for p in posts)
        likes_score = self._normalize_likes(total_likes, max_likes)

        final_score = composite * self._config.composite_weight + likes_score * self._config.likes_weight

        return AgentReviewResult(
            agent_id=agent_id,
            agent_name=agent_name,
            logic_score=float(negotiated.logic_score),
            creativity_score=float(negotiated.creativity_score),
            social_score=float(negotiated.social_score),
            composite_score=round(composite, 2),
            likes_count=total_likes,
            likes_score=round(likes_score, 2),
            final_score=round(final_score, 2),
            post_count=post_count,
            summary=negotiated.summary,
            adjustment_notes=negotiated.adjustment_notes,
        )

    def _format_posts(self, posts: list[Post]) -> str:
        """
        Format posts into a text block for reviewers.
        Truncates from the front if total length exceeds budget.
        """
        lines: list[str] = []
        for i, post in enumerate(posts, 1):
            lines.append(f"[Post {i}]\n{post.content}")
        full_text = "\n\n".join(lines)

        if len(full_text) <= self._config.max_post_chars:
            return full_text

        # Keep as many recent posts as fit in the budget
        kept: list[str] = []
        budget = self._config.max_post_chars
        for block in reversed(lines):
            if len(block) + 2 > budget:
                break
            kept.insert(0, block)
            budget -= len(block) + 2

        prefix = f"[Earlier posts omitted — showing {len(kept)} of {len(posts)}]\n\n"
        return prefix + "\n\n".join(kept)

    @staticmethod
    def _normalize_likes(total_likes: int, max_likes: int) -> float:
        """Scale likes to a 1–10 score relative to the top agent in the topic."""
        if max_likes == 0:
            return 5.0  # neutral when nobody has likes
        return 1.0 + (total_likes / max_likes) * 9.0

    def _require_app(self) -> "IdavollApp":
        if self._app is None:
            raise RuntimeError("ReviewPlugin is not installed — call app.use(ReviewPlugin()) first")
        return self._app

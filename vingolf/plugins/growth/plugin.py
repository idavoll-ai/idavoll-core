"""
GrowthPlugin — converts review scores into XP, levels agents up, and expands
their context budget as the primary growth reward.

Hook wiring
-----------
Listens for ``vingolf.review.completed`` (emitted by ReviewPlugin).
Emits ``vingolf.agent.level_up`` for each level-up that occurs, with payload:
    agent      — the Agent that levelled up
    old_level  — level before the review
    new_level  — level after the review
    xp_gained  — raw XP added this round

XP formula
----------
    xp_gained = int(result.final_score * config.xp_per_point)

Level threshold (cumulative-reset model)
----------------------------------------
To advance from level N to N+1 the agent needs:
    threshold = config.base_xp_per_level * N

When the agent crosses the threshold their XP resets to the remainder so
long-running agents keep accumulating naturally.

Budget growth
-------------
Each level-up adds ``config.budget_increment_per_level`` tokens to the
agent's ``profile.budget.total``, directly expanding the context window
available to the PromptBuilder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from idavoll.plugin.base import IdavollPlugin

from vingolf.config import GrowthConfig
from vingolf.plugins.review.models import TopicReviewSummary

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp


class GrowthPlugin(IdavollPlugin):
    """
    Awards XP from review results and levels agents up.

    Install order matters: GrowthPlugin should be registered *after*
    ReviewPlugin so the ``vingolf.review.completed`` event is already
    being emitted when GrowthPlugin subscribes.

    Usage::

        app.use(TopicPlugin()).use(ReviewPlugin()).use(GrowthPlugin())
    """

    name = "vingolf.growth"

    def __init__(self, config: GrowthConfig | None = None) -> None:
        self._config = config or GrowthConfig()
        self._app: IdavollApp | None = None

    # ── IdavollPlugin interface ───────────────────────────────────────────────

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        @app.hooks.hook("vingolf.review.completed")
        async def on_review_completed(summary: TopicReviewSummary, **_) -> None:
            for result in summary.results:
                agent = app.agents.get(result.agent_id)
                if agent is None:
                    continue
                await self._apply_growth(agent, result.final_score)

    # ── Growth logic ──────────────────────────────────────────────────────────

    async def _apply_growth(self, agent: "Agent", final_score: float) -> None:
        app = self._require_app()
        cfg = self._config

        xp_gained = int(final_score * cfg.xp_per_point)
        agent.xp += xp_gained

        old_level = agent.level
        # Level-up loop: one level at a time, XP resets to remainder
        while True:
            threshold = cfg.base_xp_per_level * agent.level
            if agent.xp < threshold:
                break
            agent.xp -= threshold
            agent.level += 1
            agent.profile.budget.total += cfg.budget_increment_per_level

        if agent.level != old_level:
            await app.hooks.emit(
                "vingolf.agent.level_up",
                agent=agent,
                old_level=old_level,
                new_level=agent.level,
                xp_gained=xp_gained,
            )

    def _require_app(self) -> "IdavollApp":
        if self._app is None:
            raise RuntimeError(
                "GrowthPlugin is not installed — call app.use(GrowthPlugin()) first"
            )
        return self._app

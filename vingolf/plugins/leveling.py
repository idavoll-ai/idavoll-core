from __future__ import annotations

from typing import TYPE_CHECKING

from idavoll.plugin.base import IdavollPlugin
from vingolf.config import LevelingConfig
from vingolf.progress import AgentProgress, AgentProgressStore
from .review import TopicReviewSummary

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp


class LevelingPlugin(IdavollPlugin):
    """Converts review scores into XP, levels, and budget growth."""

    name = "vingolf.leveling"

    def __init__(self, config: LevelingConfig | None = None) -> None:
        self._config = config or LevelingConfig()
        self._app: IdavollApp | None = None
        self._progress = AgentProgressStore()

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        @app.hooks.hook("review.completed")
        async def on_review_completed(summary: TopicReviewSummary, **_) -> None:
            for result in summary.results:
                agent = app.agents.get(result.agent_id)
                if agent is None:
                    continue
                await self._apply(agent, result.final_score)

    def get_progress(self, agent_id: str) -> AgentProgress | None:
        return self._progress.get(agent_id)

    async def _apply(self, agent: "Agent", final_score: float) -> None:
        assert self._app is not None
        progress = self._progress.get_or_create(agent.id)
        xp_gained = int(final_score * self._config.xp_per_point)
        old_level = progress.level
        progress.xp += xp_gained

        while progress.xp >= self._config.base_xp_per_level * progress.level:
            progress.xp -= self._config.base_xp_per_level * progress.level
            progress.level += 1
            agent.profile.budget.total += self._config.budget_increment_per_level

        if progress.level != old_level:
            await self._app.hooks.emit(
                "agent.level_up",
                agent=agent,
                progress=progress,
                old_level=old_level,
                new_level=progress.level,
                xp_gained=xp_gained,
            )


GrowthPlugin = LevelingPlugin

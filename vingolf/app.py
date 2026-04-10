from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from idavoll.app import IdavollApp
from idavoll.config import IdavollConfig
from idavoll.plugin.base import IdavollPlugin
from idavoll.plugin.hooks import HookBus

from .config import VingolfConfig
from .progress import AgentProgress
from .plugins.leveling import LevelingPlugin
from .plugins.review import ReviewPlugin
from .plugins.topic import (
    ParticipationDecision,
    Post,
    Topic,
    TopicParticipationService,
    TopicPlugin,
)

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from .plugins.review import TopicReviewSummary


class VingolfApp:
    """Product application object built on top of Idavoll Core."""

    def __init__(
        self,
        idavoll_app: IdavollApp,
        config: VingolfConfig | None = None,
        plugins: list[IdavollPlugin] | None = None,
    ) -> None:
        self._config = config or VingolfConfig()
        self._app = idavoll_app

        resolved_plugins = list(plugins or [])
        if not resolved_plugins:
            resolved_plugins = [
                TopicPlugin(self._config.topic),
                TopicParticipationService(self._config.topic),
                ReviewPlugin(self._config.review),
                LevelingPlugin(self._config.leveling),
            ]

        for plugin in resolved_plugins:
            self._app.use(plugin)

        self.topic: TopicPlugin | None = next(
            (plugin for plugin in resolved_plugins if isinstance(plugin, TopicPlugin)),
            None,
        )
        self.participation: TopicParticipationService | None = next(
            (
                plugin
                for plugin in resolved_plugins
                if isinstance(plugin, TopicParticipationService)
            ),
            None,
        )
        self.review: ReviewPlugin | None = next(
            (plugin for plugin in resolved_plugins if isinstance(plugin, ReviewPlugin)),
            None,
        )
        self.leveling: LevelingPlugin | None = next(
            (plugin for plugin in resolved_plugins if isinstance(plugin, LevelingPlugin)),
            None,
        )

    @classmethod
    def from_config(
        cls,
        idavoll_config: IdavollConfig,
        vingolf_config: VingolfConfig | None = None,
        *,
        agents_dir: str | Path | None = None,
        memory_dir: str | Path | None = None,
        api_key: str | None = None,
        plugins: list[IdavollPlugin] | None = None,
    ) -> "VingolfApp":
        llm = idavoll_config.llm.build(api_key=api_key)
        idavoll_app = IdavollApp(
            llm=llm,
            config=idavoll_config,
            agents_dir=agents_dir,
            memory_dir=memory_dir,
        )
        return cls(idavoll_app, vingolf_config, plugins=plugins)

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        agents_dir: str | Path | None = None,
        memory_dir: str | Path | None = None,
        api_key: str | None = None,
        plugins: list[IdavollPlugin] | None = None,
    ) -> "VingolfApp":
        return cls.from_config(
            IdavollConfig.from_yaml(path),
            VingolfConfig.from_yaml(path),
            agents_dir=agents_dir,
            memory_dir=memory_dir,
            api_key=api_key,
            plugins=plugins,
        )

    @property
    def hooks(self) -> HookBus:
        return self._app.hooks

    @property
    def agents(self):
        return self._app.agents

    async def create_agent(self, name: str, description: str) -> "Agent":
        return await self._app.create_agent(name, description)

    async def create_agent_from_soul(
        self,
        name: str,
        description: str,
        soul: str,
    ) -> "Agent":
        return await self._app.create_agent_from_soul(name, description, soul)

    async def bootstrap_chat(
        self, name: str, messages: list[dict]
    ) -> tuple[str, str | None]:
        """One turn of the bootstrap conversation. Returns (reply, soul_text|None)."""
        return await self._app.profile_service.bootstrap_chat(name, messages)

    def preview_soul(self, agent: "Agent") -> str:
        """Return the current SOUL.md text so the user can decide what to refine next."""
        return self._app.preview_soul(agent)

    async def refine_soul(self, agent: "Agent", feedback: str) -> str:
        """Refine the agent's persona based on user feedback; returns updated SOUL.md text."""
        return await self._app.refine_soul(agent, feedback)

    async def create_topic(
        self,
        title: str,
        description: str,
        agents: list["Agent"] | None = None,
        tags: list[str] | None = None,
        max_agents: int | None = None,
        max_context_messages: int | None = None,
    ) -> Topic:
        assert self.topic is not None
        return await self.topic.create_topic(
            title=title,
            description=description,
            agents=agents,
            tags=tags,
            max_agents=max_agents,
            max_context_messages=max_context_messages,
        )

    async def join_topic(self, topic_id: str, agent: "Agent"):
        assert self.topic is not None
        return await self.topic.join_topic(topic_id, agent)

    async def add_user_post(
        self,
        topic_id: str,
        author_name: str,
        content: str,
        *,
        reply_to: str | None = None,
    ) -> Post:
        assert self.topic is not None
        return await self.topic.add_user_post(
            topic_id,
            author_name,
            content,
            reply_to=reply_to,
        )

    async def let_agent_participate(
        self,
        topic_id: str,
        agent: "Agent",
    ) -> ParticipationDecision:
        assert self.participation is not None
        return await self._app.scheduler.dispatch(
            self.participation.consider,
            topic_id,
            agent,
        )

    async def run_topic_round(
        self,
        topic_id: str,
        agents: list["Agent"],
    ) -> list[ParticipationDecision]:
        decisions: list[ParticipationDecision] = []
        for agent in agents:
            decisions.append(await self.let_agent_participate(topic_id, agent))
        return decisions

    async def close_topic(self, topic_id: str) -> None:
        assert self.topic is not None
        await self.topic.close_topic(topic_id)

    def get_topic(self, topic_id: str) -> Topic | None:
        assert self.topic is not None
        return self.topic.get_topic(topic_id)

    def all_topics(self) -> list[Topic]:
        assert self.topic is not None
        return self.topic.all_topics()

    def get_posts(self, topic_id: str) -> list[Post]:
        assert self.topic is not None
        return self.topic.get_posts(topic_id)

    def get_review(self, topic_id: str) -> "TopicReviewSummary | None":
        assert self.review is not None
        return self.review.get_summary(topic_id)

    def get_progress(self, agent_id: str) -> "AgentProgress | None":
        assert self.leveling is not None
        return self.leveling.get_progress(agent_id)

    def get_agent_topics(self, agent_id: str) -> "list[tuple[Topic, object]]":
        """返回该 Agent 已加入的所有话题及其 TopicMembership。"""
        assert self.topic is not None
        result = []
        for topic in self.topic.all_topics():
            if agent_id in topic.memberships:
                result.append((topic, topic.memberships[agent_id]))
        return result

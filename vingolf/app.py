from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from idavoll.app import IdavollApp
from idavoll.config import IdavollConfig
from idavoll.hook.base import IdavollPlugin
from idavoll.hook.hooks import HookBus

from .config import VingolfConfig
from .persistence import (
    AgentProfileRepository,
    AgentProgressRepository,
    Database,
    ReviewRepository,
    SessionRecordRepository,
    SQLiteSessionSearch,
    TopicRepository,
)
from .services import ConsolidationService
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

        # Persistence layer — initialised lazily by startup()
        self._db: Database | None = None
        self._agent_repo: AgentProfileRepository | None = None
        self._topic_repo: TopicRepository | None = None
        self._progress_repo: AgentProgressRepository | None = None
        self._review_repo: ReviewRepository | None = None
        self._session_repo: SessionRecordRepository | None = None
        self.consolidation: ConsolidationService | None = None

        resolved_plugins = list(plugins or [])
        if not resolved_plugins:
            resolved_plugins = [
                TopicPlugin(self._config.topic),
                TopicParticipationService(self._config.topic),
                ReviewPlugin(self._config.review, self._config.review_plan),
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

    async def startup(self) -> None:
        """Open the database, wire repos to plugins, and restore persisted state.

        Call once after constructing ``VingolfApp``, before serving requests.
        Integrates with FastAPI's ``lifespan`` or any async startup hook.
        """
        db = Database(self._config.db_path)
        await db.init()
        self._db = db
        self._agent_repo = AgentProfileRepository(db)
        self._topic_repo = TopicRepository(db)
        self._progress_repo = AgentProgressRepository(db)
        self._review_repo = ReviewRepository(db)
        self._session_repo = SessionRecordRepository(db)
        self.consolidation = ConsolidationService(self._app, self._review_repo)

        # Wire repos into plugins
        if self.topic is not None:
            self.topic.repo = self._topic_repo
        if self.leveling is not None:
            self.leveling._repo = self._progress_repo
        if self.review is not None:
            self.review._repo = self._review_repo

        # Register AgentLoader so Core can restore profiles from DB
        self._app.set_agent_loader(self._agent_repo.get)

        # Hook: persist profile whenever an agent is created
        @self._app.hooks.hook("agent.created")
        async def _on_agent_created(agent, **_) -> None:
            await self._agent_repo.save(agent.profile)  # type: ignore[union-attr]
            self._attach_session_search(agent)

        # Hook: attach SQLiteSessionSearch when an agent is loaded from DB
        @self._app.hooks.hook("agent.loaded")
        async def _on_agent_loaded(agent, **_) -> None:
            self._attach_session_search(agent)

        # Hook: persist budget changes (level-up expands budget.total)
        @self._app.hooks.hook("agent.level_up")
        async def _on_level_up(agent, **_) -> None:
            await self._agent_repo.save(agent.profile)  # type: ignore[union-attr]

        # Hook: persist raw session conversation to SQLite for all closed sessions
        @self._app.hooks.hook("on_session_end")
        async def _on_session_end(session, **_) -> None:
            await self._persist_session_record(session)

        # Topic.close_topic() emits topic.closed directly without going through
        # IdavollApp.close_session(), so keep this hook to cover topic-backed
        # sessions as well.
        @self._app.hooks.hook("topic.closed")
        async def _on_topic_closed(topic, session, **_) -> None:
            del topic
            await self._persist_session_record(session)

        # Restore all known agents into the registry
        for profile in await self._agent_repo.all():
            if self._app.agents.get(profile.id) is None:
                agent = self._app.agents.register(profile)
                try:
                    workspace = self._app.workspaces.load(profile.id)
                    self._app._attach_runtime(agent, workspace)
                except FileNotFoundError:
                    pass  # workspace not on disk; agent metadata still usable
                self._attach_session_search(agent)

        # Restore topic + leveling state
        if self.topic is not None:
            await self.topic.load_state()
        if self.leveling is not None:
            await self.leveling.load_state()

    def _attach_session_search(self, agent) -> None:
        """Replace the no-op SessionSearch with the SQLite-backed implementation."""
        if self._session_repo is not None:
            agent.session_search = SQLiteSessionSearch(
                self._session_repo,
                agent_id=agent.id,
                llm=self._app.llm,
            )

    async def _persist_session_record(self, session) -> None:
        """Upsert one raw closed-session transcript into SQLite."""
        if self._session_repo is None or not session.messages:
            return

        participants = ",".join(
            getattr(p, "id", str(p)) for p in session.participants
        )
        conversation = "\n".join(
            f"[{m.role}] {m.agent_name}: {m.content}"
            for m in session.messages
            if (m.content or "").strip()
        )
        if not conversation:
            return

        await self._session_repo.save(
            session_id=session.id,
            participants=participants,
            conversation=conversation,
        )

    async def shutdown(self) -> None:
        """Close the database connection gracefully."""
        if self._db is not None:
            await self._db.close()
            self._db = None

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
        return await self._app.bootstrap_chat(name, messages)

    def bootstrap_chat_stream(self, name: str, messages: list[dict]):
        """Streaming variant — returns an async generator of SSE lines."""
        return self._app.bootstrap_chat_stream(name, messages)

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
        return await self.participation.consider(topic_id, agent)

    async def run_topic_round(
        self,
        topic_id: str,
        agents: list["Agent"],
    ) -> list[ParticipationDecision]:
        decisions: list[ParticipationDecision] = []
        for agent in agents:
            decisions.append(await self.let_agent_participate(topic_id, agent))
        return decisions

    async def run_agent_rounds(
        self,
        topic_id: str,
        agent: "Agent",
        rounds: int,
    ) -> list[ParticipationDecision]:
        """让同一个 Agent 在同一个 topic 里连续参与 *rounds* 次。

        每轮调用 ``let_agent_participate``，遇到 ignore 决策时继续执行，
        直到所有轮次完成或额度耗尽为止。
        """
        decisions: list[ParticipationDecision] = []
        for _ in range(rounds):
            decision = await self.let_agent_participate(topic_id, agent)
            decisions.append(decision)
            # 若额度耗尽则不必继续
            if decision.action == "ignore" and decision.reason == "quota exhausted":
                break
        return decisions

    async def like_post(self, topic_id: str, post_id: str) -> Post:
        assert self.topic is not None
        return await self.topic.like_post(topic_id, post_id)

    async def close_topic(self, topic_id: str) -> None:
        assert self.topic is not None
        await self.topic.close_topic(topic_id)

    async def reopen_topic(self, topic_id: str) -> Topic:
        assert self.topic is not None
        topic = await self.topic.reopen_topic(topic_id)
        if self.review is not None:
            self.review.clear_summary(topic_id)
        return topic

    async def delete_topic(self, topic_id: str) -> None:
        assert self.topic is not None
        topic = self.topic.get_topic(topic_id)
        if topic is None:
            raise KeyError(f"Topic {topic_id!r} not found")
        session_id = topic.session_id
        await self.topic.delete_topic(topic_id)
        if self.review is not None:
            self.review.clear_summary(topic_id)
        if self._session_repo is not None:
            await self._session_repo.delete(session_id)

    async def delete_agent(self, agent_id: str) -> None:
        agent = self._app.agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id!r} not found")

        if self.topic is not None:
            await self.topic.remove_agent_from_topics(agent_id)

        if self.leveling is not None:
            self.leveling._progress._items.pop(agent_id, None)
        self._app.agents.delete(agent_id)
        self._app.workspaces.delete(agent_id)

        if self._progress_repo is not None:
            await self._progress_repo.delete(agent_id)
        if self._agent_repo is not None:
            await self._agent_repo.delete(agent_id)

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

    async def get_review_records_for_agent(
        self, agent_id: str, *, limit: int = 50
    ) -> list[dict]:
        if self._review_repo is None:
            return []
        return await self._review_repo.get_review_records_for_agent(
            agent_id, limit=limit
        )

    async def get_review_records_for_topic(
        self, topic_id: str, *, limit: int = 50
    ) -> list[dict]:
        if self._review_repo is None:
            return []
        return await self._review_repo.get_review_records_for_topic(
            topic_id, limit=limit
        )

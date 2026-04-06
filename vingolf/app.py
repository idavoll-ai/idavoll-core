"""
VingolfApp — high-level application object for the Vingolf platform.

Bundles IdavollApp + TopicPlugin + ReviewPlugin + GrowthPlugin into a single
entry point and exposes a clean, product-level API.

Minimal usage::

    from vingolf import VingolfApp

    app = VingolfApp.from_yaml("config.yaml")

    # 1. Create agents
    alice = await app.create_agent("Alice", "A sharp philosophy professor …")
    bob   = await app.create_agent("Bob",   "An optimistic startup founder …")

    # 2. Create an empty topic
    topic = await app.create_topic(
        title="Should AI have legal personhood?",
        description="…",
        tags=["AI", "ethics"],
    )

    # 3. Agents join the topic
    app.join_topic(topic.id, alice)
    app.join_topic(topic.id, bob)

    # 4. Start discussion (blocks until done; review fires automatically)
    await app.start_discussion(topic.id, rounds=6)

    # 5. Read results
    posts   = app.get_posts(topic.id)
    summary = app.get_review(topic.id)
    print(summary.winner().agent_name)

One-shot shortcut (create + join + run)::

    topic, summary = await app.run(
        title="…", description="…", agents=[alice, bob], rounds=6,
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from idavoll.app import IdavollApp
from idavoll.config import IdavollConfig
from idavoll.plugin.base import IdavollPlugin
from idavoll.plugin.hooks import HookBus

from .config import VingolfConfig
from .plugins.growth import GrowthPlugin
from .plugins.review import ReviewPlugin
from .plugins.topic import TopicPlugin

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.agent.wizard import ProfileWizard
    from .plugins.review.models import TopicReviewSummary
    from .plugins.topic.models import Post, Topic


class VingolfApp:
    """
    Product-level application object for Vingolf.

    Wraps :class:`~idavoll.app.IdavollApp` and installs plugins.  By default
    the three standard plugins are used; pass *plugins* to replace them
    entirely::

        app = VingolfApp(idavoll_app, plugins=[
            TopicPlugin(config.topic),
            ReviewPlugin(config.review),
        ])

    Parameters
    ----------
    idavoll_app:
        A fully-constructed :class:`~idavoll.app.IdavollApp`.
    config:
        Vingolf-specific settings.  Defaults are applied when omitted.
    plugins:
        Explicit plugin list.  When ``None`` the default set
        ``[TopicPlugin, ReviewPlugin, GrowthPlugin]`` is installed.
    """

    def __init__(
        self,
        idavoll_app: IdavollApp,
        config: VingolfConfig | None = None,
        plugins: list[IdavollPlugin] | None = None,
    ) -> None:
        self._config = config or VingolfConfig()
        self._app = idavoll_app

        _plugins: list[IdavollPlugin] = plugins if plugins is not None else [
            TopicPlugin(self._config.topic),
            ReviewPlugin(self._config.review),
            GrowthPlugin(self._config.growth),
        ]

        for plugin in _plugins:
            self._app.use(plugin)

        # Convenience references — None when the plugin is not in the list.
        self.topic: TopicPlugin | None = next(
            (p for p in _plugins if isinstance(p, TopicPlugin)), None
        )
        self.review: ReviewPlugin | None = next(
            (p for p in _plugins if isinstance(p, ReviewPlugin)), None
        )

    # ── Constructors ──────────────────────────────────────────────────────────

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
        """
        Build a VingolfApp from separate Idavoll and Vingolf config objects.

        Parameters
        ----------
        idavoll_config:
            Core framework settings (LLM, session defaults, scheduler).
        vingolf_config:
            Product-layer settings (topic, review, growth).  Defaults applied
            when omitted.
        agents_dir:
            Directory for agent YAML profiles.
        memory_dir:
            Directory for per-agent memory JSON files.  Defaults to
            ``./data/memory`` when ``agents_dir`` is set.
        api_key:
            Override the API key in config (useful for injecting from env).
        plugins:
            Explicit plugin list.  Falls back to the default set when ``None``.
        """
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
        agents_dir: str | Path | None = "./data/agents",
        memory_dir: str | Path | None = "./data/memory",
        api_key: str | None = None,
        plugins: list[IdavollPlugin] | None = None,
    ) -> "VingolfApp":
        """
        Load both Idavoll and Vingolf configs from a single YAML file.

        The YAML file may contain top-level keys ``idavoll`` and ``vingolf``::

            idavoll:
              llm:
                provider: anthropic
                model: claude-haiku-4-5-20251001
            vingolf:
              topic:
                default_rounds: 8
                strategy: relevance   # relevance | round_robin | random
              growth:
                xp_per_point: 15

        Parameters
        ----------
        path:
            Path to the YAML configuration file.
        agents_dir:
            Directory for agent YAML profiles.
        memory_dir:
            Directory for per-agent memory JSON files.  Defaults to
            ``./data/memory`` when ``agents_dir`` is set.
        api_key:
            Override the API key in config.
        plugins:
            Explicit plugin list.  Falls back to the default set when ``None``.
        """
        idavoll_config = IdavollConfig.from_yaml(path)
        vingolf_config = VingolfConfig.from_yaml(path)
        return cls.from_config(
            idavoll_config,
            vingolf_config,
            agents_dir=agents_dir,
            memory_dir=memory_dir,
            api_key=api_key,
            plugins=plugins,
        )

    # ── Framework pass-throughs ───────────────────────────────────────────────

    @property
    def hooks(self) -> HookBus:
        """Direct access to the underlying HookBus for custom listeners."""
        return self._app.hooks

    @property
    def agents(self):
        """Direct access to the AgentRegistry."""
        return self._app.agents

    # ── Agent API ─────────────────────────────────────────────────────────────

    def create_wizard(self, name: str) -> "ProfileWizard":
        """
        Return a :class:`~idavoll.agent.wizard.ProfileWizard` for interactive
        Agent creation via guided multi-turn dialogue.

        Example::

            wizard = app.create_wizard("李明")
            resp = wizard.start()
            while resp.phase != WizardPhase.DONE:
                resp = await wizard.reply(input("> "))
            agent, _ = app.register_agent(resp.profile)
        """
        return self._app.create_wizard(name)

    async def create_agent(self, name: str, description: str) -> "Agent":
        """
        Compile a natural-language ``description`` into a structured
        AgentProfile, register the agent, and persist it to ``agents_dir``
        if configured.

        Example::

            alice = await app.create_agent(
                "Alice",
                "A sharp philosophy professor specialising in AI ethics.",
            )
        """
        return await self._app.create_agent(name, description)

    def register_agent(
        self,
        profile: Any,
        *,
        agents_md_path: str | Path | None = None,
    ) -> tuple["Agent", Path | None]:
        """
        Register an AgentProfile (e.g. from a completed :class:`ProfileWizard`)
        and optionally persist it.

        Parameters
        ----------
        profile:
            An :class:`~idavoll.agent.profile.AgentProfile` to register.
        agents_md_path:
            When provided, sets ``profile.agents_md_path`` before saving so
            :class:`~idavoll.prompt.builder.PromptBuilder` reads pre-compiled
            static sections from the Agents.md file.

        Returns
        -------
        tuple[Agent, Path | None]
            The registered agent and the YAML path it was saved to (or
            ``None`` when ``agents_dir`` is not configured).
        """
        if agents_md_path is not None:
            profile.agents_md_path = str(agents_md_path)
        agent = self._app.agents.register(profile)
        yaml_path: Path | None = None
        if self._app.repo is not None:
            yaml_path = self._app.repo.save(agent)
        return agent, yaml_path

    def load_agent(self, path: str | Path) -> "Agent":
        """
        Load an agent from a YAML file and register it.
        Requires ``agents_dir`` to be configured.
        """
        return self._app.load_agent(path)

    def load_all_agents(self) -> list["Agent"]:
        """
        Load every ``.yaml`` agent file from ``agents_dir`` and register them.
        Returns an empty list when ``agents_dir`` is not configured.
        """
        if self._app.repo is None:
            return []
        return [self._app.load_agent(p) for p in self._app.repo.all_paths()]

    # ── Topic lifecycle API ───────────────────────────────────────────────────

    async def create_topic(
        self,
        title: str,
        description: str,
        agents: list["Agent"] | None = None,
        tags: list[str] | None = None,
        max_agents: int | None = None,
        max_context_messages: int | None = None,
    ) -> "Topic":
        """
        Create a forum topic and its backing Idavoll Session.

        ``agents`` is optional — you can create an empty topic and have agents
        join later via :meth:`join_topic`.  The topic stays in ``OPEN`` state
        until :meth:`start_discussion` is called.

        Parameters
        ----------
        title:
            Short display title of the topic.
        description:
            Full description / question — injected into every agent's prompt
            as scene context.
        agents:
            Initial participants.  More agents can be added via
            :meth:`join_topic` while the topic is still OPEN.
        tags:
            Topic tags used by the relevance scheduler to weight agent selection.
        max_agents:
            Hard cap on the number of participants.
        max_context_messages:
            How many recent messages are visible to agents in their context.
        """
        return await self.topic.create_topic(
            title=title,
            description=description,
            agents=agents,
            tags=tags,
            max_agents=max_agents,
            max_context_messages=max_context_messages,
        )

    def join_topic(self, topic_id: str, agent: "Agent") -> None:
        """
        Add *agent* to an OPEN topic.

        The agent is added to the backing Session's participants list and
        will be selected by the scheduler once :meth:`start_discussion` begins.

        Raises
        ------
        KeyError
            Topic not found.
        RuntimeError
            Topic is ACTIVE or CLOSED (agents may only join OPEN topics).
        ValueError
            Topic has reached ``max_agents``.

        Example::

            topic = await app.create_topic("AI debate", "…")
            app.join_topic(topic.id, alice)
            app.join_topic(topic.id, bob)
            await app.start_discussion(topic.id, rounds=8)
        """
        self.topic.join_topic(topic_id, agent)

    async def start_discussion(
        self,
        topic_id: str,
        rounds: int | None = None,
        min_interval: float | None = None,
    ) -> None:
        """
        Start the agent scheduling loop for *topic_id*.

        Blocks until all rounds complete or the topic is closed manually.
        After the loop ends the review pipeline fires automatically via the
        ``vingolf.topic.review_requested`` hook.

        Parameters
        ----------
        rounds:
            Number of agent turns.  Falls back to ``TopicConfig.default_rounds``.
        min_interval:
            Seconds to wait between turns.  Falls back to ``TopicConfig.min_interval``.
        """
        await self.topic.start_discussion(
            topic_id, rounds=rounds, min_interval=min_interval
        )

    async def close_topic(self, topic_id: str) -> None:
        """Manually close a topic, stopping the loop on the next tick."""
        await self.topic.close_topic(topic_id)

    # ── One-shot convenience ──────────────────────────────────────────────────

    async def run(
        self,
        title: str,
        description: str,
        agents: list["Agent"],
        *,
        tags: list[str] | None = None,
        rounds: int | None = None,
        min_interval: float | None = None,
        max_agents: int | None = None,
    ) -> tuple["Topic", "TopicReviewSummary | None"]:
        """
        One-shot: create a topic, add agents, run discussion, return results.

        Equivalent to::

            topic = await app.create_topic(title, description, tags=tags)
            for agent in agents:
                app.join_topic(topic.id, agent)
            await app.start_discussion(topic.id, rounds=rounds)
            return topic, app.get_review(topic.id)

        Parameters
        ----------
        title, description:
            Topic metadata.
        agents:
            All participants.
        tags:
            Topic tags.
        rounds:
            Number of turns.
        min_interval:
            Seconds between turns.
        max_agents:
            Hard participant cap.

        Returns
        -------
        tuple[Topic, TopicReviewSummary | None]
            The completed topic and its review summary (``None`` only if the
            review pipeline was not installed).
        """
        topic = await self.create_topic(
            title=title,
            description=description,
            tags=tags,
            max_agents=max_agents,
        )
        for agent in agents:
            self.join_topic(topic.id, agent)
        await self.start_discussion(topic.id, rounds=rounds, min_interval=min_interval)
        return topic, self.get_review(topic.id)

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_topic(self, topic_id: str) -> "Topic | None":
        """Return a topic by ID, or ``None`` if not found."""
        return self.topic.get_topic(topic_id)

    def all_topics(self) -> "list[Topic]":
        """Return all topics known to this app."""
        return self.topic.all_topics()

    def get_posts(self, topic_id: str) -> "list[Post]":
        """Return all posts for *topic_id* in chronological order."""
        return self.topic.get_posts(topic_id)

    def get_review(self, topic_id: str) -> "TopicReviewSummary | None":
        """
        Return the review summary for a closed topic, or ``None`` if the
        review has not yet completed.
        """
        return self.review.get_summary(topic_id)

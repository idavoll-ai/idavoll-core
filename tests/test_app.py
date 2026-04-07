"""
Integration tests for IdavollApp using a FakeLLM — no API calls required.

These tests verify the full scheduling loop, hook firing, and plugin wiring
without touching the network.
"""
from __future__ import annotations

import pytest

from idavoll import IdavollApp, IdavollConfig
from idavoll.agent.profile import AgentProfile
from idavoll.session.session import SessionState
from vingolf.config import VingolfConfig
from vingolf.plugins.review import ReviewPlugin
from vingolf.plugins.topic import TopicPlugin


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_app(fake_llm, config: IdavollConfig | None = None) -> IdavollApp:
    return IdavollApp(llm=fake_llm, config=config)


async def make_agent(app: IdavollApp, fake_llm, name: str) -> object:
    """Create an agent. FakeLLM returns a default _AgentProfileData automatically."""
    return await app.create_agent(name, "A test agent.")


# ── IdavollApp bootstrap ───────────────────────────────────────────────────────

class TestIdavollAppInit:
    def test_default_scheduler_is_round_robin(self, fake_llm):
        from idavoll.scheduler.strategies import RoundRobinStrategy
        app = make_app(fake_llm)
        assert isinstance(app.scheduler, RoundRobinStrategy)

    def test_config_sets_random_scheduler(self, fake_llm):
        from idavoll.scheduler.strategies import RandomStrategy
        cfg = IdavollConfig(scheduler={"strategy": "random"})
        app = make_app(fake_llm, config=cfg)
        assert isinstance(app.scheduler, RandomStrategy)

    def test_from_config_factory(self, fake_llm, monkeypatch):
        """from_config builds the LLM via config.llm.build()."""
        from idavoll.config import LLMConfig
        monkeypatch.setattr(LLMConfig, "build", lambda self, **_: fake_llm)
        cfg = IdavollConfig()
        app = IdavollApp.from_config(cfg)
        assert app.llm.raw is fake_llm


# ── Session loop ──────────────────────────────────────────────────────────────

class TestRunSession:
    @pytest.mark.asyncio
    async def test_session_produces_messages(self, fake_llm):
        app = make_app(fake_llm)
        alice = await make_agent(app, fake_llm, "Alice")
        bob = await make_agent(app, fake_llm, "Bob")

        session = app.sessions.create(participants=[alice, bob])
        await app.run_session(session, rounds=4, min_interval=0.0)

        assert session.state == SessionState.CLOSED
        assert len(session.messages) == 4
        assert all(m.content == fake_llm.reply for m in session.messages)

    @pytest.mark.asyncio
    async def test_session_uses_config_defaults(self, fake_llm):
        cfg = IdavollConfig(session={"default_rounds": 2, "min_interval": 0.0})
        app = make_app(fake_llm, config=cfg)
        alice = await make_agent(app, fake_llm, "Alice")

        session = app.sessions.create(participants=[alice])
        await app.run_session(session)  # no explicit rounds

        assert len(session.messages) == 2

    @pytest.mark.asyncio
    async def test_hooks_fire_in_order(self, fake_llm):
        app = make_app(fake_llm)
        alice = await make_agent(app, fake_llm, "Alice")

        log: list[str] = []

        @app.hooks.hook("session.created")
        async def on_created(**_): log.append("created")

        @app.hooks.hook("session.message.after")
        async def on_msg(**_): log.append("message")

        @app.hooks.hook("session.closed")
        async def on_closed(**_): log.append("closed")

        session = app.sessions.create(participants=[alice])
        await app.run_session(session, rounds=2, min_interval=0.0)

        assert log[0] == "created"
        assert log[-1] == "closed"
        assert log.count("message") == 2


# ── TopicPlugin + ReviewPlugin integration ────────────────────────────────────

class TestTopicAndReviewPlugins:
    @pytest.mark.asyncio
    async def test_full_discussion_and_review(self, fake_llm):
        vingolf_cfg = VingolfConfig(
            topic={"default_rounds": 2, "min_interval": 0.0},
            review={"max_post_chars": 5000},
        )

        topic_plugin = TopicPlugin(config=vingolf_cfg.topic)
        review_plugin = ReviewPlugin(config=vingolf_cfg.review)

        app = make_app(fake_llm)
        app.use(topic_plugin).use(review_plugin)

        alice = await make_agent(app, fake_llm, "Alice")
        bob = await make_agent(app, fake_llm, "Bob")

        topic = await topic_plugin.create_topic(
            title="Test topic",
            description="A topic for testing.",
            agents=[alice, bob],
            tags=["test"],
        )

        completed_event = {}

        @app.hooks.hook("vingolf.review.completed")
        async def on_done(summary, **_):
            completed_event["summary"] = summary

        await topic_plugin.start_discussion(topic.id)

        summary = review_plugin.get_summary(topic.id)
        assert summary is not None
        assert summary.topic_title == "Test topic"
        # Only agents who actually posted get reviewed — TopicRelevanceStrategy
        # may pick the same agent multiple times, so compare against post authors.
        posts = topic_plugin.get_posts(topic.id)
        unique_authors = {p.agent_id for p in posts}
        assert len(summary.results) == len(unique_authors)
        assert all(r.final_score > 0 for r in summary.results)
        assert "summary" in completed_event

    @pytest.mark.asyncio
    async def test_topic_plugin_uses_config_defaults(self, fake_llm):
        cfg = VingolfConfig(topic={"default_rounds": 3, "min_interval": 0.0})
        topic_plugin = TopicPlugin(config=cfg.topic)

        app = make_app(fake_llm)
        app.use(topic_plugin)

        alice = await make_agent(app, fake_llm, "Alice")
        topic = await topic_plugin.create_topic(
            title="T", description="D", agents=[alice]
        )
        await topic_plugin.start_discussion(topic.id)  # uses default_rounds=3

        posts = topic_plugin.get_posts(topic.id)
        assert len(posts) == 3

    @pytest.mark.asyncio
    async def test_review_config_max_post_chars(self, fake_llm):
        """ReviewPlugin respects max_post_chars from config."""
        from vingolf.config import ReviewConfig
        cfg = ReviewConfig(max_post_chars=10)
        review_plugin = ReviewPlugin(config=cfg)
        assert review_plugin._config.max_post_chars == 10

    def test_review_plugin_shorthand_override(self):
        """max_post_chars kwarg overrides config."""
        from vingolf.config import ReviewConfig
        cfg = ReviewConfig(max_post_chars=3000)
        plugin = ReviewPlugin(config=cfg, max_post_chars=500)
        assert plugin._config.max_post_chars == 500


# ── GrowthPlugin ──────────────────────────────────────────────────────────────

class TestGrowthPlugin:
    @pytest.mark.asyncio
    async def test_xp_awarded_after_review(self, fake_llm):
        """Agents receive XP proportional to their final_score."""
        from vingolf.config import GrowthConfig, VingolfConfig
        from vingolf.plugins.growth import GrowthPlugin

        cfg = VingolfConfig(
            topic={"default_rounds": 2, "min_interval": 0.0},
            growth={"xp_per_point": 10, "base_xp_per_level": 1000},  # high threshold → no level-up
        )
        topic_plugin = TopicPlugin(config=cfg.topic)
        review_plugin = ReviewPlugin(config=cfg.review)
        growth_plugin = GrowthPlugin(config=cfg.growth)

        app = make_app(fake_llm)
        app.use(topic_plugin).use(review_plugin).use(growth_plugin)

        alice = await make_agent(app, fake_llm, "Alice")
        topic = await topic_plugin.create_topic(
            title="T", description="D", agents=[alice]
        )
        await topic_plugin.start_discussion(topic.id)

        # FakeLLM returns score=7 for each dimension → negotiated = 7/7/7
        # composite = 7.0, likes_score = 5.0 (no likes, neutral),
        # final_score = 7.0*0.5 + 5.0*0.5 = 6.0  → xp = int(6.0 * 10) = 60
        assert alice.xp > 0

    @pytest.mark.asyncio
    async def test_level_up_expands_budget(self, fake_llm):
        """Crossing the XP threshold increments level and expands context budget."""
        from vingolf.config import GrowthConfig, TopicConfig
        from vingolf.plugins.growth import GrowthPlugin

        # Very low threshold so FakeLLM scores definitely cause level-up
        cfg = GrowthConfig(xp_per_point=100, base_xp_per_level=1)

        topic_plugin = TopicPlugin(config=TopicConfig(default_rounds=1, min_interval=0.0))
        review_plugin = ReviewPlugin()
        growth_plugin = GrowthPlugin(config=cfg)

        app = make_app(fake_llm)
        app.use(topic_plugin).use(review_plugin).use(growth_plugin)

        alice = await make_agent(app, fake_llm, "Alice")
        initial_budget = alice.profile.budget.total
        initial_level = alice.level  # 1

        topic = await topic_plugin.create_topic(
            title="T", description="D", agents=[alice]
        )
        await topic_plugin.start_discussion(topic.id)

        assert alice.level > initial_level
        assert alice.profile.budget.total > initial_budget

    @pytest.mark.asyncio
    async def test_level_up_event_emitted(self, fake_llm):
        """vingolf.agent.level_up fires when an agent crosses the XP threshold."""
        from vingolf.config import GrowthConfig, TopicConfig
        from vingolf.plugins.growth import GrowthPlugin

        cfg = GrowthConfig(xp_per_point=100, base_xp_per_level=1)

        topic_plugin = TopicPlugin(config=TopicConfig(default_rounds=1, min_interval=0.0))
        review_plugin = ReviewPlugin()
        growth_plugin = GrowthPlugin(config=cfg)

        app = make_app(fake_llm)
        app.use(topic_plugin).use(review_plugin).use(growth_plugin)

        level_up_events: list[dict] = []

        @app.hooks.hook("vingolf.agent.level_up")
        async def on_level_up(agent, old_level, new_level, xp_gained, **_):
            level_up_events.append({"agent": agent, "old": old_level, "new": new_level})

        alice = await make_agent(app, fake_llm, "Alice")
        topic = await topic_plugin.create_topic(
            title="T", description="D", agents=[alice]
        )
        await topic_plugin.start_discussion(topic.id)

        assert len(level_up_events) > 0
        assert level_up_events[0]["old"] < level_up_events[0]["new"]

    @pytest.mark.asyncio
    async def test_no_level_up_below_threshold(self, fake_llm):
        """Agents below the XP threshold stay at level 1."""
        from vingolf.config import GrowthConfig, TopicConfig
        from vingolf.plugins.growth import GrowthPlugin

        # Unreachably high threshold
        cfg = GrowthConfig(xp_per_point=1, base_xp_per_level=99999)

        topic_plugin = TopicPlugin(config=TopicConfig(default_rounds=1, min_interval=0.0))
        review_plugin = ReviewPlugin()
        growth_plugin = GrowthPlugin(config=cfg)

        app = make_app(fake_llm)
        app.use(topic_plugin).use(review_plugin).use(growth_plugin)

        level_up_events: list = []

        @app.hooks.hook("vingolf.agent.level_up")
        async def on_level_up(**_):
            level_up_events.append(True)

        alice = await make_agent(app, fake_llm, "Alice")
        topic = await topic_plugin.create_topic(
            title="T", description="D", agents=[alice]
        )
        await topic_plugin.start_discussion(topic.id)

        assert alice.level == 1
        assert len(level_up_events) == 0


# ── join_topic (Session.add_participant + TopicPlugin) ────────────────────────

class TestJoinTopic:
    @pytest.mark.asyncio
    async def test_join_before_discussion(self, fake_llm):
        """Agents joined after create_topic participate in the discussion."""
        from vingolf.config import TopicConfig
        topic_plugin = TopicPlugin(config=TopicConfig(default_rounds=2, min_interval=0.0))
        app = make_app(fake_llm)
        app.use(topic_plugin)

        alice = await make_agent(app, fake_llm, "Alice")
        bob = await make_agent(app, fake_llm, "Bob")

        # Create with no initial agents
        topic = await topic_plugin.create_topic(title="T", description="D")
        assert topic.agent_count == 0

        await topic_plugin.join_topic(topic.id, alice)
        await topic_plugin.join_topic(topic.id, bob)
        assert topic.agent_count == 2
        assert alice.id in topic.agent_ids
        assert bob.id in topic.agent_ids

        await topic_plugin.start_discussion(topic.id)
        posts = topic_plugin.get_posts(topic.id)
        assert len(posts) == 2

    @pytest.mark.asyncio
    async def test_join_is_idempotent(self, fake_llm):
        """Joining the same agent twice does not duplicate it."""
        topic_plugin = TopicPlugin()
        app = make_app(fake_llm)
        app.use(topic_plugin)
        alice = await make_agent(app, fake_llm, "Alice")

        topic = await topic_plugin.create_topic(title="T", description="D")
        await topic_plugin.join_topic(topic.id, alice)
        await topic_plugin.join_topic(topic.id, alice)  # second join — should be no-op

        assert topic.agent_count == 1

        session = app.sessions.get_or_raise(topic.session_id)
        assert len(session.participants) == 1

    @pytest.mark.asyncio
    async def test_join_closed_topic_raises(self, fake_llm):
        """Joining a CLOSED topic raises RuntimeError at the product layer.

        Session.add_participant no longer enforces OPEN-only; the constraint is
        now owned by TopicPlugin so that the framework stays policy-free.
        """
        from vingolf.config import TopicConfig
        topic_plugin = TopicPlugin(config=TopicConfig(default_rounds=1, min_interval=0.0))
        app = make_app(fake_llm)
        app.use(topic_plugin)
        alice = await make_agent(app, fake_llm, "Alice")
        bob = await make_agent(app, fake_llm, "Bob")

        topic = await topic_plugin.create_topic(title="T", description="D", agents=[alice])
        await topic_plugin.start_discussion(topic.id)  # now CLOSED

        # TopicPlugin enforces the lifecycle constraint; the error message
        # says "OPEN" (the required lifecycle state).
        with pytest.raises(RuntimeError, match="OPEN"):
            await topic_plugin.join_topic(topic.id, bob)

    @pytest.mark.asyncio
    async def test_join_respects_max_agents(self, fake_llm):
        """Joining beyond max_agents raises ValueError."""
        topic_plugin = TopicPlugin()
        app = make_app(fake_llm)
        app.use(topic_plugin)

        agents = [await make_agent(app, fake_llm, f"Agent{i}") for i in range(3)]

        topic = await topic_plugin.create_topic(
            title="T", description="D", max_agents=2
        )
        await topic_plugin.join_topic(topic.id, agents[0])
        await topic_plugin.join_topic(topic.id, agents[1])

        with pytest.raises(ValueError, match="full"):
            await topic_plugin.join_topic(topic.id, agents[2])


# ── VingolfApp integration ────────────────────────────────────────────────────

class TestVingolfApp:
    @pytest.mark.asyncio
    async def test_join_topic_via_vingolf_app(self, fake_llm):
        """VingolfApp.join_topic delegates to TopicPlugin correctly."""
        from vingolf import VingolfApp
        from vingolf.config import VingolfConfig, TopicConfig

        cfg = VingolfConfig(topic=TopicConfig(default_rounds=2, min_interval=0.0))
        vapp = VingolfApp(IdavollApp(llm=fake_llm), config=cfg)

        alice = await vapp.create_agent("Alice", "test")
        bob = await vapp.create_agent("Bob", "test")

        topic = await vapp.create_topic("T", "D")
        await vapp.join_topic(topic.id, alice)
        await vapp.join_topic(topic.id, bob)

        await vapp.start_discussion(topic.id)

        posts = vapp.get_posts(topic.id)
        assert len(posts) == 2
        assert vapp.get_review(topic.id) is not None

    @pytest.mark.asyncio
    async def test_run_shortcut(self, fake_llm):
        """VingolfApp.run() creates topic, joins agents, runs, returns results."""
        from vingolf import VingolfApp
        from vingolf.config import VingolfConfig, TopicConfig

        cfg = VingolfConfig(topic=TopicConfig(default_rounds=2, min_interval=0.0))
        vapp = VingolfApp(IdavollApp(llm=fake_llm), config=cfg)

        alice = await vapp.create_agent("Alice", "test")
        bob = await vapp.create_agent("Bob", "test")

        topic, summary = await vapp.run(
            title="T", description="D",
            agents=[alice, bob],
            rounds=2, min_interval=0.0,
        )

        assert topic is not None
        assert summary is not None
        assert len(vapp.get_posts(topic.id)) == 2

    @pytest.mark.asyncio
    async def test_topic_agent_ids_tracked(self, fake_llm):
        """Topic.agent_ids is populated for agents passed at create_topic time."""
        from vingolf import VingolfApp
        from vingolf.config import VingolfConfig, TopicConfig

        cfg = VingolfConfig(topic=TopicConfig(default_rounds=1, min_interval=0.0))
        vapp = VingolfApp(IdavollApp(llm=fake_llm), config=cfg)

        alice = await vapp.create_agent("Alice", "test")
        topic = await vapp.create_topic("T", "D", agents=[alice])

        assert alice.id in topic.agent_ids

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

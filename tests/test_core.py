"""Tests for core framework components: Session, HookBus, schedulers, registry."""
from __future__ import annotations

import pytest

from idavoll.agent.profile import AgentProfile
from idavoll.agent.registry import Agent, AgentRegistry
from idavoll.plugin.hooks import HookBus
from idavoll.scheduler.strategies import RandomStrategy, RoundRobinStrategy
from idavoll.session.session import Message, Session, SessionState


# ── Session ───────────────────────────────────────────────────────────────────

class TestSession:
    def test_initial_state(self, alice, bob):
        session = Session(participants=[alice, bob])
        assert session.state == SessionState.OPEN
        assert len(session.messages) == 0
        assert session.participants == [alice, bob]

    def test_add_message(self, alice):
        session = Session(participants=[alice])
        msg = Message(agent_id=alice.id, agent_name=alice.profile.name, content="Hello")
        session.add_message(msg)
        assert len(session.messages) == 1
        assert session.messages[0].content == "Hello"

    def test_close(self, alice):
        session = Session(participants=[alice])
        session.close()
        assert session.state == SessionState.CLOSED

    def test_context_window_respects_limit(self, alice):
        session = Session(participants=[alice], max_context_messages=3)
        for i in range(5):
            session.add_message(Message(agent_id=alice.id, agent_name="Alice", content=f"msg {i}"))
        # all messages are stored in full
        assert len(session.messages) == 5
        # context window only keeps the last 3
        assert len(session.context.get_raw()) == 3


# ── AgentRegistry ─────────────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_register_and_get(self, alice_profile):
        registry = AgentRegistry()
        agent = registry.register(alice_profile)
        assert registry.get(agent.id) is agent

    def test_get_missing_returns_none(self):
        registry = AgentRegistry()
        assert registry.get("nonexistent") is None

    def test_get_or_raise(self, alice_profile):
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            registry.get_or_raise("nonexistent")

    def test_all(self, alice_profile, bob_profile):
        registry = AgentRegistry()
        a = registry.register(alice_profile)
        b = registry.register(bob_profile)
        assert set(registry.all()) == {a, b}

    def test_remove(self, alice_profile):
        registry = AgentRegistry()
        agent = registry.register(alice_profile)
        registry.remove(agent.id)
        assert registry.get(agent.id) is None

    def test_update(self, alice_profile):
        registry = AgentRegistry()
        agent = registry.register(alice_profile)
        registry.update(agent.id, lambda a: setattr(a, "xp", 42))
        assert registry.get(agent.id).xp == 42


# ── Schedulers ────────────────────────────────────────────────────────────────

class TestRoundRobinStrategy:
    def test_cycles_in_order(self, alice, bob):
        session = Session(participants=[alice, bob])
        strategy = RoundRobinStrategy()

        picks = [strategy.select_next(session, [alice, bob]) for _ in range(4)]
        assert picks == [alice, bob, alice, bob]

    def test_should_continue_open(self, alice):
        session = Session(participants=[alice])
        strategy = RoundRobinStrategy()
        assert strategy.should_continue(session) is True

    def test_should_stop_when_closed(self, alice):
        session = Session(participants=[alice])
        session.close()
        strategy = RoundRobinStrategy()
        assert strategy.should_continue(session) is False


class TestRandomStrategy:
    def test_returns_a_participant(self, alice, bob):
        session = Session(participants=[alice, bob])
        strategy = RandomStrategy()
        for _ in range(10):
            pick = strategy.select_next(session, [alice, bob])
            assert pick in (alice, bob)

    def test_should_continue_open(self, alice):
        session = Session(participants=[alice])
        strategy = RandomStrategy()
        assert strategy.should_continue(session) is True


# ── HookBus ───────────────────────────────────────────────────────────────────

class TestHookBus:
    @pytest.mark.asyncio
    async def test_hook_receives_kwargs(self):
        bus = HookBus()
        received = {}

        @bus.hook("test.event")
        async def handler(x, y, **_):
            received["x"] = x
            received["y"] = y

        await bus.emit("test.event", x=1, y=2)
        assert received == {"x": 1, "y": 2}

    @pytest.mark.asyncio
    async def test_multiple_handlers_all_called(self):
        bus = HookBus()
        calls = []

        @bus.hook("evt")
        async def h1(**_): calls.append("h1")

        @bus.hook("evt")
        async def h2(**_): calls.append("h2")

        await bus.emit("evt")
        assert "h1" in calls and "h2" in calls

    @pytest.mark.asyncio
    async def test_emit_unknown_event_is_noop(self):
        bus = HookBus()
        # Should not raise
        await bus.emit("no.listeners.here")

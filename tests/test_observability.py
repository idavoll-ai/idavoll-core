"""
Tests for idavoll/observability — MetricsCollector, JSONFormatter,
ObservabilityPlugin, and configure_logging.

All tests run against FakeLLM so no API key is required.
"""
from __future__ import annotations

import json
import logging

import pytest

from idavoll import IdavollApp
from idavoll.agent.profile import AgentProfile
from idavoll.agent.registry import Agent
from idavoll.observability import (
    ObservabilityPlugin,
    configure_logging,
    JSONFormatter,
    MetricsCollector,
)
from idavoll.session.session import Message, Session


# ── MetricsCollector ──────────────────────────────────────────────────────────

class TestMetricsCollector:
    def test_counter_starts_at_zero(self):
        m = MetricsCollector()
        assert m.counter("missing.key") == 0

    def test_increment(self):
        m = MetricsCollector()
        m.increment("sessions.total")
        m.increment("sessions.total")
        assert m.counter("sessions.total") == 2

    def test_increment_by(self):
        m = MetricsCollector()
        m.increment("messages.total_chars", by=100)
        m.increment("messages.total_chars", by=50)
        assert m.counter("messages.total_chars") == 150

    def test_histogram_empty_summary(self):
        m = MetricsCollector()
        summary = m.histogram("llm.latency_ms")
        assert summary["count"] == 0
        assert summary["min"] == 0.0

    def test_histogram_single_value(self):
        m = MetricsCollector()
        m.record("llm.latency_ms", 500.0)
        s = m.histogram("llm.latency_ms")
        assert s["count"] == 1
        assert s["min"] == 500.0
        assert s["max"] == 500.0
        assert s["mean"] == 500.0

    def test_histogram_multiple_values(self):
        m = MetricsCollector()
        for v in [100.0, 200.0, 300.0, 400.0, 500.0]:
            m.record("llm.latency_ms", v)
        s = m.histogram("llm.latency_ms")
        assert s["count"] == 5
        assert s["min"] == 100.0
        assert s["max"] == 500.0
        assert s["mean"] == 300.0

    def test_snapshot_contains_counters_and_histograms(self):
        m = MetricsCollector()
        m.increment("llm.calls", by=3)
        m.record("session.duration_s", 10.0)
        snap = m.snapshot()
        assert snap["counters"]["llm.calls"] == 3
        assert snap["histograms"]["session.duration_s"]["count"] == 1

    def test_reset_clears_all(self):
        m = MetricsCollector()
        m.increment("sessions.total")
        m.record("llm.latency_ms", 100.0)
        m.reset()
        assert m.counter("sessions.total") == 0
        assert m.histogram("llm.latency_ms")["count"] == 0


# ── JSONFormatter ─────────────────────────────────────────────────────────────

class TestJSONFormatter:
    def _make_record(self, msg: str = "test", extra: dict | None = None) -> logging.LogRecord:
        record = logging.LogRecord(
            name="idavoll",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in (extra or {}).items():
            setattr(record, k, v)
        return record

    def test_output_is_valid_json(self):
        fmt = JSONFormatter()
        record = self._make_record("hello")
        line = fmt.format(record)
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self):
        fmt = JSONFormatter()
        record = self._make_record("hello")
        parsed = json.loads(fmt.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed

    def test_level_name_correct(self):
        fmt = JSONFormatter()
        record = self._make_record("x")
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "INFO"

    def test_extra_fields_merged_into_output(self):
        fmt = JSONFormatter()
        record = self._make_record("llm.generate.after", extra={
            "event": "llm.generate.after",
            "agent_name": "Alice",
            "latency_ms": 312.5,
        })
        parsed = json.loads(fmt.format(record))
        assert parsed["event"] == "llm.generate.after"
        assert parsed["agent_name"] == "Alice"
        assert parsed["latency_ms"] == 312.5

    def test_message_is_the_log_string(self):
        fmt = JSONFormatter()
        record = self._make_record("session.created")
        parsed = json.loads(fmt.format(record))
        assert parsed["message"] == "session.created"


# ── configure_logging ─────────────────────────────────────────────────────────

class TestConfigureLogging:
    def test_sets_idavoll_logger_level(self):
        configure_logging(level=logging.DEBUG)
        idavoll_logger = logging.getLogger("idavoll")
        assert idavoll_logger.level == logging.DEBUG

    def test_json_false_uses_plain_formatter(self, capsys):
        import io
        stream = io.StringIO()
        configure_logging(level=logging.WARNING, json=False, stream=stream)
        logging.getLogger("idavoll").warning("plain test message")
        output = stream.getvalue()
        # Plain format should not be valid JSON
        assert "plain test message" in output

    def test_logger_does_not_propagate(self):
        configure_logging()
        assert logging.getLogger("idavoll").propagate is False


# ── ObservabilityPlugin (unit) ────────────────────────────────────────────────

class TestObservabilityPluginUnit:
    """Direct handler invocation — no app or real sessions needed."""

    def _make_session(self, *names: str) -> Session:
        profiles = [AgentProfile(name=n) for n in names]
        agents = [Agent(p) for p in profiles]
        return Session(participants=agents)

    def test_session_created_increments_counter(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice")
        obs._on_session_created(session=session)
        assert obs.metrics.counter("sessions.total") == 1

    def test_session_closed_records_duration(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice")
        obs._on_session_created(session=session)
        session.close()
        obs._on_session_closed(session=session)
        assert obs.metrics.counter("sessions.closed") == 1
        assert obs.metrics.histogram("session.duration_s")["count"] == 1

    def test_session_closed_counts_messages(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice")
        session.add_message(Message(agent_id="1", agent_name="Alice", content="hi"))
        session.add_message(Message(agent_id="1", agent_name="Alice", content="bye"))
        obs._on_session_created(session=session)
        session.close()
        obs._on_session_closed(session=session)
        assert obs.metrics.counter("messages.total") == 2

    def test_message_after_tracks_char_count(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice")
        msg = Message(agent_id="1", agent_name="Alice", content="hello world")
        obs._on_message_after(session=session, message=msg)
        assert obs.metrics.counter("messages.total_chars") == len("hello world")

    def test_llm_generate_after_records_latency(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice")
        agent = session.participants[0]
        obs._on_llm_generate_after(
            agent=agent, session=session, latency_ms=750.0, content_length=100
        )
        assert obs.metrics.counter("llm.calls") == 1
        assert obs.metrics.histogram("llm.latency_ms")["count"] == 1
        assert obs.metrics.histogram("llm.latency_ms")["mean"] == 750.0

    def test_llm_calls_tracked_per_agent(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice", "Bob")
        alice, bob = session.participants
        obs._on_llm_generate_after(agent=alice, session=session, latency_ms=100.0, content_length=50)
        obs._on_llm_generate_after(agent=alice, session=session, latency_ms=200.0, content_length=60)
        obs._on_llm_generate_after(agent=bob, session=session, latency_ms=300.0, content_length=70)
        assert obs.metrics.counter("llm.calls_by_agent.Alice") == 2
        assert obs.metrics.counter("llm.calls_by_agent.Bob") == 1

    def test_agent_created_increments_counter(self):
        obs = ObservabilityPlugin()
        agent = Agent(AgentProfile(name="Alice"))
        obs._on_agent_created(agent=agent)
        assert obs.metrics.counter("agents.created") == 1

    def test_scheduler_selected_tracks_per_agent(self):
        obs = ObservabilityPlugin()
        session = self._make_session("Alice", "Bob")
        alice, bob = session.participants
        obs._on_scheduler_selected(session=session, agent=alice)
        obs._on_scheduler_selected(session=session, agent=alice)
        obs._on_scheduler_selected(session=session, agent=bob)
        assert obs.metrics.counter("scheduler.selections.Alice") == 2
        assert obs.metrics.counter("scheduler.selections.Bob") == 1

    def test_events_logged_to_idavoll_logger(self):
        """Verify ObservabilityPlugin writes to the 'idavoll' logger.

        caplog relies on propagation, which configure_logging() disables.
        Use a temporary in-process handler instead.
        """
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture(level=logging.INFO)
        idavoll_logger = logging.getLogger("idavoll")
        idavoll_logger.addHandler(handler)
        try:
            obs = ObservabilityPlugin(log_level=logging.INFO)
            session = self._make_session("Alice")
            obs._on_session_created(session=session)
        finally:
            idavoll_logger.removeHandler(handler)

        assert any("session.created" in r.getMessage() for r in records)


# ── ObservabilityPlugin (integration) ────────────────────────────────────────

class TestObservabilityPluginIntegration:
    """Full app.run_session loop with ObservabilityPlugin installed."""

    def _make_app(self, fake_llm) -> tuple[IdavollApp, ObservabilityPlugin]:
        obs = ObservabilityPlugin()
        app = IdavollApp(llm=fake_llm)
        app.use(obs)
        return app, obs

    @pytest.mark.asyncio
    async def test_session_metrics_after_run(self, fake_llm):
        app, obs = self._make_app(fake_llm)
        alice = Agent(AgentProfile(name="Alice"))
        bob = Agent(AgentProfile(name="Bob"))
        app.agents._agents[alice.id] = alice
        app.agents._agents[bob.id] = bob

        session = app.sessions.create(participants=[alice, bob])
        await app.run_session(session, rounds=4, min_interval=0.0)

        snap = obs.metrics.snapshot()
        assert snap["counters"]["sessions.total"] == 1
        assert snap["counters"]["sessions.closed"] == 1
        assert snap["counters"]["messages.total"] == 4
        assert snap["counters"]["llm.calls"] == 4
        assert snap["histograms"]["llm.latency_ms"]["count"] == 4
        assert snap["histograms"]["session.duration_s"]["count"] == 1

    @pytest.mark.asyncio
    async def test_llm_latency_is_positive(self, fake_llm):
        app, obs = self._make_app(fake_llm)
        alice = Agent(AgentProfile(name="Alice"))
        app.agents._agents[alice.id] = alice

        session = app.sessions.create(participants=[alice])
        await app.run_session(session, rounds=2, min_interval=0.0)

        latency = obs.metrics.histogram("llm.latency_ms")
        assert latency["min"] >= 0.0

    @pytest.mark.asyncio
    async def test_multiple_sessions_accumulate_metrics(self, fake_llm):
        app, obs = self._make_app(fake_llm)
        alice = Agent(AgentProfile(name="Alice"))
        app.agents._agents[alice.id] = alice

        for _ in range(3):
            session = app.sessions.create(participants=[alice])
            await app.run_session(session, rounds=2, min_interval=0.0)

        assert obs.metrics.counter("sessions.total") == 3
        assert obs.metrics.counter("sessions.closed") == 3
        assert obs.metrics.counter("llm.calls") == 6

    @pytest.mark.asyncio
    async def test_snapshot_is_serialisable(self, fake_llm):
        """snapshot() must be JSON-serialisable for logging/export pipelines."""
        app, obs = self._make_app(fake_llm)
        alice = Agent(AgentProfile(name="Alice"))
        app.agents._agents[alice.id] = alice

        session = app.sessions.create(participants=[alice])
        await app.run_session(session, rounds=1, min_interval=0.0)

        snap = obs.metrics.snapshot()
        dumped = json.dumps(snap)
        assert json.loads(dumped) == snap

    @pytest.mark.asyncio
    async def test_llm_generate_after_hook_fires(self, fake_llm):
        """llm.generate.after is emitted by app.run_session and received by plugin."""
        app, obs = self._make_app(fake_llm)
        alice = Agent(AgentProfile(name="Alice"))
        app.agents._agents[alice.id] = alice

        received: list[dict] = []

        @app.hooks.hook("llm.generate.after")
        async def on_llm(agent, session, latency_ms, content_length, **_):
            received.append({
                "agent": agent.profile.name,
                "latency_ms": latency_ms,
                "content_length": content_length,
            })

        session = app.sessions.create(participants=[alice])
        await app.run_session(session, rounds=2, min_interval=0.0)

        assert len(received) == 2
        assert all(r["agent"] == "Alice" for r in received)
        assert all(r["content_length"] > 0 for r in received)
        assert all(r["latency_ms"] >= 0.0 for r in received)

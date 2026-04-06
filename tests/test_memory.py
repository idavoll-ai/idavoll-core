"""Tests for the long-term memory system: models, repository, and consolidator."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from idavoll.agent.consolidator import MemoryConsolidator, _ExtractedEntries
from idavoll.agent.memory import AgentMemory, MemoryCategory, MemoryEntry, MemoryPlan
from idavoll.agent.profile import AgentProfile, IdentityConfig
from idavoll.agent.registry import Agent
from idavoll.agent.repository import AgentRepository
from idavoll.session.session import Message, Session


# ── MemoryPlan ────────────────────────────────────────────────────────────────

class TestMemoryPlan:
    def test_get_category_found(self):
        plan = MemoryPlan(categories=[
            MemoryCategory(name="beliefs", description="my beliefs", max_entries=5),
        ])
        cat = plan.get_category("beliefs")
        assert cat is not None
        assert cat.max_entries == 5

    def test_get_category_missing(self):
        plan = MemoryPlan(categories=[])
        assert plan.get_category("beliefs") is None


# ── AgentMemory ───────────────────────────────────────────────────────────────

class TestAgentMemory:
    def _entry(self, content: str) -> MemoryEntry:
        return MemoryEntry(content=content, formed_at="2024-01-01")

    def test_add_and_get(self):
        mem = AgentMemory()
        mem.add("beliefs", self._entry("AI should be regulated"), max_entries=10)
        assert len(mem.get("beliefs")) == 1

    def test_max_entries_trims_oldest(self):
        mem = AgentMemory()
        for i in range(5):
            mem.add("beliefs", self._entry(f"belief {i}"), max_entries=3)
        entries = mem.get("beliefs")
        assert len(entries) == 3
        # Newest 3 are kept
        assert entries[0].content == "belief 2"
        assert entries[-1].content == "belief 4"

    def test_to_context_text_empty(self):
        mem = AgentMemory()
        plan = MemoryPlan(categories=[
            MemoryCategory(name="beliefs", description="test", max_entries=10),
        ])
        assert mem.to_context_text(plan, max_tokens=500) == ""

    def test_to_context_text_renders_entries(self):
        mem = AgentMemory()
        mem.add("beliefs", MemoryEntry(content="AI needs guardrails", formed_at="2024-01-15"), max_entries=10)
        plan = MemoryPlan(categories=[
            MemoryCategory(name="beliefs", description="test", max_entries=10),
        ])
        text = mem.to_context_text(plan, max_tokens=500)
        assert "长期记忆" in text
        assert "beliefs" in text
        assert "AI needs guardrails" in text

    def test_to_context_text_respects_budget(self):
        mem = AgentMemory()
        long_content = "x" * 200
        for _ in range(10):
            mem.add("beliefs", MemoryEntry(content=long_content, formed_at="2024-01-01"), max_entries=20)
        plan = MemoryPlan(categories=[
            MemoryCategory(name="beliefs", description="test", max_entries=20),
        ])
        text = mem.to_context_text(plan, max_tokens=50)
        # Should not exceed roughly 50 * 3 chars
        assert len(text) <= 50 * 3 + 100  # some slack for header


# ── AgentRepository ───────────────────────────────────────────────────────────

class TestAgentRepository:
    def _make_agent(self, name: str = "TestAgent") -> Agent:
        profile = AgentProfile(
            name=name,
            identity=IdentityConfig(role="A test agent", backstory="", goal="Test"),
            memory_plan=MemoryPlan(categories=[
                MemoryCategory(name="notes", description="test notes", max_entries=5),
            ]),
        )
        agent = Agent(profile)
        agent.memory.add("notes", MemoryEntry(content="first note", formed_at="2024-01-01"), max_entries=5)
        return agent

    def _make_repo(self, tmpdir: str) -> AgentRepository:
        from pathlib import Path
        memory_dir = Path(tmpdir) / "memory"
        return AgentRepository(tmpdir, memory_dir=memory_dir)

    def test_save_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            agent = self._make_agent()
            path = repo.save(agent)
            assert path.exists()

    def test_save_creates_memory_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            agent = self._make_agent("MemJson")
            repo.save(agent)
            mem_path = repo.memory_path_for_name("MemJson")
            assert mem_path.exists()
            import json
            data = json.loads(mem_path.read_text())
            assert data["entries"]["notes"][0]["content"] == "first note"

    def test_yaml_has_no_memory_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            agent = self._make_agent("NoMemYaml")
            yaml_path = repo.save(agent)
            import yaml as _yaml
            data = _yaml.safe_load(yaml_path.read_text())
            assert "memory" not in data

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            agent = self._make_agent("RoundTrip")
            repo.save(agent)

            path = repo.path_for_name("RoundTrip")
            profile2, memory2 = repo.load(path)

            assert profile2.name == "RoundTrip"
            assert profile2.identity.role == "A test agent"
            assert profile2.memory_plan.categories[0].name == "notes"
            assert len(memory2.get("notes")) == 1
            assert memory2.get("notes")[0].content == "first note"

    def test_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            agent = self._make_agent("ExistsTest")
            assert not repo.exists("ExistsTest")
            repo.save(agent)
            assert repo.exists("ExistsTest")

    def test_all_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._make_repo(tmpdir)
            repo.save(self._make_agent("Alpha"))
            repo.save(self._make_agent("Beta"))
            paths = repo.all_paths()
            assert len(paths) == 2


# ── MemoryConsolidator ────────────────────────────────────────────────────────

class TestMemoryConsolidator:
    def _make_agent_with_plan(self) -> Agent:
        profile = AgentProfile(
            name="Scholar",
            identity=IdentityConfig(role="A scholar", backstory="", goal="Learn"),
            memory_plan=MemoryPlan(categories=[
                MemoryCategory(name="insights", description="key insights", max_entries=10),
            ]),
        )
        return Agent(profile)

    def _make_session_with_messages(self, agent: Agent) -> Session:
        session = Session(participants=[agent])
        session.add_message(Message(
            agent_id=agent.id,
            agent_name=agent.profile.name,
            content="I think AI regulation is critical for safety.",
        ))
        return session

    def _make_fake_llm(self, entries: list[str]):
        """FakeLLM that returns _ExtractedEntries with the given content."""
        result = _ExtractedEntries(entries=entries)
        chain = MagicMock()
        async def ainvoke(_input, **_kw):
            return result
        chain.ainvoke = ainvoke
        llm = MagicMock()
        llm.with_structured_output.return_value = chain
        return llm

    @pytest.mark.asyncio
    async def test_consolidate_adds_entries(self):
        agent = self._make_agent_with_plan()
        session = self._make_session_with_messages(agent)
        llm = self._make_fake_llm(["AI regulation is critical for safety."])

        # Patch the chain directly
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        inner_chain = MagicMock()
        async def ainvoke(_input, **_kw):
            return _ExtractedEntries(entries=["AI regulation is critical for safety."])
        inner_chain.ainvoke = ainvoke
        consolidator._chain = inner_chain

        await consolidator.consolidate(agent, session)
        assert len(agent.memory.get("insights")) == 1
        assert "regulation" in agent.memory.get("insights")[0].content

    @pytest.mark.asyncio
    async def test_consolidate_skips_empty_plan(self):
        profile = AgentProfile(name="Empty")
        agent = Agent(profile)
        session = Session(participants=[agent])

        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator._chain = MagicMock()

        await consolidator.consolidate(agent, session)
        # chain should never be called
        consolidator._chain.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_consolidate_skips_empty_transcript(self):
        agent = self._make_agent_with_plan()
        session = Session(participants=[agent])  # no messages

        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator._chain = MagicMock()

        await consolidator.consolidate(agent, session)
        consolidator._chain.ainvoke.assert_not_called()

"""Tests for Agent tools and skills functionality.

Covers:
- ToolRegistry: register, get, scan via @tool decorator
- ToolsetManager: define toolsets, resolve, build_index, unlock_toolset
- AgentRegistry: register, unlock_toolset
- SkillsLibrary: create, get, patch, archive, list_active, build_index
- Builtin tools: skill_get, skill_patch (sync); memory, session_search (async)
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from idavoll.agent.profile import AgentProfile, IdentityConfig, SoulSpec, VoiceConfig
from idavoll.agent.registry import Agent, AgentRegistry
from idavoll.agent.workspace import ProfileWorkspace, ProfileWorkspaceManager
from idavoll.skills.library import SkillsLibrary
from idavoll.tools.registry import (
    ToolRegistry,
    ToolSpec,
    Toolset,
    ToolsetManager,
    tool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def manager(registry: ToolRegistry) -> ToolsetManager:
    return ToolsetManager(registry)


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> ProfileWorkspace:
    """Create a minimal workspace on disk."""
    ws_dir = tmp_path / "agent-ws"
    ws_dir.mkdir()
    (ws_dir / "skills").mkdir()
    (ws_dir / "sessions").mkdir()
    return ProfileWorkspace(ws_dir)


@pytest.fixture
def skills_lib(tmp_workspace: ProfileWorkspace) -> SkillsLibrary:
    return SkillsLibrary(tmp_workspace)


@pytest.fixture
def sample_profile() -> AgentProfile:
    return AgentProfile(name="TestAgent", description="用于单元测试的 Agent")


@pytest.fixture
def sample_agent(sample_profile: AgentProfile) -> Agent:
    return Agent(profile=sample_profile)


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_register_and_get(self, registry: ToolRegistry) -> None:
        spec = ToolSpec(name="ping", description="Ping tool")
        registry.register(spec)
        assert registry.get("ping") is spec

    def test_get_missing_returns_none(self, registry: ToolRegistry) -> None:
        assert registry.get("nonexistent") is None

    def test_get_or_raise_missing(self, registry: ToolRegistry) -> None:
        with pytest.raises(KeyError, match="not found"):
            registry.get_or_raise("nonexistent")

    def test_all_and_names(self, registry: ToolRegistry) -> None:
        registry.register(ToolSpec(name="a", description="A"))
        registry.register(ToolSpec(name="b", description="B"))
        assert set(registry.names()) == {"a", "b"}
        assert len(registry.all()) == 2

    def test_re_register_replaces(self, registry: ToolRegistry) -> None:
        registry.register(ToolSpec(name="x", description="old"))
        registry.register(ToolSpec(name="x", description="new"))
        assert registry.get("x").description == "new"
        assert len(registry) == 1

    def test_tool_decorator_sets_spec(self) -> None:
        @tool(name="greet", description="Greet someone")
        def greet(name: str) -> str:
            return f"Hello {name}"

        spec = getattr(greet, "__tool_spec__")
        assert isinstance(spec, ToolSpec)
        assert spec.name == "greet"
        assert spec.description == "Greet someone"
        assert spec.fn is greet

    def test_tool_decorator_uses_docstring(self) -> None:
        @tool()
        def say_hi(name: str) -> str:
            """Say hi to someone."""
            return f"Hi {name}"

        spec = getattr(say_hi, "__tool_spec__")
        assert spec.name == "say_hi"
        assert spec.description == "Say hi to someone."

    def test_scan_module(self, registry: ToolRegistry) -> None:
        """scan_module picks up @tool-decorated functions."""
        import types

        mod = types.ModuleType("fake_mod")

        @tool(name="scan_tool", description="Scanned tool")
        def scan_tool() -> str:
            return "ok"

        mod.scan_tool = scan_tool  # type: ignore[attr-defined]

        count = registry.scan_module(mod)
        assert count == 1
        assert registry.get("scan_tool") is not None


# ---------------------------------------------------------------------------
# ToolsetManager
# ---------------------------------------------------------------------------


class TestToolsetManager:
    def _register_tools(self, registry: ToolRegistry, *names: str) -> None:
        for name in names:
            registry.register(ToolSpec(name=name, description=f"Tool {name}"))

    def test_define_and_resolve_simple(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        self._register_tools(registry, "tool_a", "tool_b")
        manager.define(Toolset(name="basic", tools=["tool_a", "tool_b"]))

        specs = manager.resolve(["basic"])
        assert [s.name for s in specs] == ["tool_a", "tool_b"]

    def test_resolve_with_includes(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        self._register_tools(registry, "alpha", "beta", "gamma")
        manager.define(Toolset(name="base", tools=["alpha", "beta"]))
        manager.define(Toolset(name="extended", includes=["base"], tools=["gamma"]))

        specs = manager.resolve(["extended"])
        assert [s.name for s in specs] == ["alpha", "beta", "gamma"]

    def test_resolve_deduplicates(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        self._register_tools(registry, "shared", "extra")
        manager.define(Toolset(name="set_a", tools=["shared"]))
        manager.define(Toolset(name="set_b", tools=["shared", "extra"]))

        specs = manager.resolve(["set_a", "set_b"])
        names = [s.name for s in specs]
        assert names.count("shared") == 1
        assert "extra" in names

    def test_resolve_disabled_tools(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        self._register_tools(registry, "keep", "remove")
        manager.define(Toolset(name="full", tools=["keep", "remove"]))

        specs = manager.resolve(["full"], disabled_tools=["remove"])
        assert [s.name for s in specs] == ["keep"]

    def test_resolve_unknown_toolset_is_silent(
        self, manager: ToolsetManager
    ) -> None:
        specs = manager.resolve(["does_not_exist"])
        assert specs == []

    def test_resolve_unknown_tool_skipped(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        registry.register(ToolSpec(name="real", description="Real"))
        manager.define(Toolset(name="mix", tools=["real", "ghost"]))

        specs = manager.resolve(["mix"])
        assert [s.name for s in specs] == ["real"]

    def test_resolve_cycle_safe(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        self._register_tools(registry, "t1")
        manager.define(Toolset(name="A", includes=["B"], tools=["t1"]))
        manager.define(Toolset(name="B", includes=["A"]))

        specs = manager.resolve(["A"])  # should not recurse infinitely
        assert [s.name for s in specs] == ["t1"]

    def test_build_index_format(
        self, registry: ToolRegistry, manager: ToolsetManager
    ) -> None:
        registry.register(ToolSpec(name="do_stuff", description="Does stuff"))
        manager.define(Toolset(name="ts", tools=["do_stuff"]))

        idx = manager.build_index(["ts"])
        assert "## Available Tools" in idx
        assert "do_stuff" in idx
        assert "Does stuff" in idx

    def test_build_index_empty(self, manager: ToolsetManager) -> None:
        assert manager.build_index([]) == ""


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class TestAgentRegistry:
    def test_register_and_get(self, sample_profile: AgentProfile) -> None:
        reg = AgentRegistry()
        agent = reg.register(sample_profile)
        assert reg.get(agent.id) is agent

    def test_get_missing_returns_none(self) -> None:
        reg = AgentRegistry()
        assert reg.get("missing") is None

    def test_get_or_raise(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(KeyError):
            reg.get_or_raise("missing")

    def test_all(self, sample_profile: AgentProfile) -> None:
        reg = AgentRegistry()
        reg.register(sample_profile)
        assert len(reg.all()) == 1

    def test_update(self, sample_profile: AgentProfile) -> None:
        reg = AgentRegistry()
        agent = reg.register(sample_profile)
        reg.update(agent.id, lambda a: a.metadata.update({"key": "val"}))
        assert reg.get(agent.id).metadata["key"] == "val"

    def test_unlock_toolset_without_toolset_manager(
        self, sample_profile: AgentProfile
    ) -> None:
        reg = AgentRegistry()
        agent = reg.register(sample_profile)
        reg.unlock_toolset(agent.id, "basic")
        assert "basic" in agent.profile.enabled_toolsets

    def test_unlock_toolset_idempotent(self, sample_profile: AgentProfile) -> None:
        reg = AgentRegistry()
        agent = reg.register(sample_profile)
        reg.unlock_toolset(agent.id, "basic")
        reg.unlock_toolset(agent.id, "basic")
        assert agent.profile.enabled_toolsets.count("basic") == 1

    def test_unlock_toolset_resolves_tools(
        self, sample_profile: AgentProfile
    ) -> None:
        registry = ToolRegistry()
        registry.register(ToolSpec(name="ping", description="Ping"))
        manager = ToolsetManager(registry)
        manager.define(Toolset(name="basic", tools=["ping"]))

        reg = AgentRegistry(toolsets=manager)
        agent = reg.register(sample_profile)
        reg.unlock_toolset(agent.id, "basic")

        assert len(agent.tools) == 1
        assert agent.tools[0].name == "ping"


# ---------------------------------------------------------------------------
# SkillsLibrary
# ---------------------------------------------------------------------------


class TestSkillsLibrary:
    def test_create_and_get(self, skills_lib: SkillsLibrary) -> None:
        skill = skills_lib.create(
            name="socratic-method",
            description="用苏格拉底式提问引导讨论",
            body="## 方法\n提出问题，引导思考。",
            tags=["reasoning"],
        )
        assert skill.name == "socratic-method"

        loaded = skills_lib.get("socratic-method")
        assert loaded is not None
        assert loaded.description == "用苏格拉底式提问引导讨论"
        assert "提出问题" in loaded.body
        assert "reasoning" in loaded.tags

    def test_create_duplicate_raises(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="dupe", description="First")
        with pytest.raises(FileExistsError):
            skills_lib.create(name="dupe", description="Second")

    def test_get_missing_returns_none(self, skills_lib: SkillsLibrary) -> None:
        assert skills_lib.get("nonexistent") is None

    def test_patch_body(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="my-skill", description="Desc", body="Old body")
        patched = skills_lib.patch("my-skill", body="New body")
        assert patched.body == "New body"
        assert skills_lib.get("my-skill").body == "New body"

    def test_patch_description(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="sk", description="Old desc")
        skills_lib.patch("sk", description="New desc")
        assert skills_lib.get("sk").description == "New desc"

    def test_patch_missing_raises(self, skills_lib: SkillsLibrary) -> None:
        with pytest.raises(FileNotFoundError):
            skills_lib.patch("missing", body="body")

    def test_archive(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="old-skill", description="Old")
        skills_lib.archive("old-skill")
        skill = skills_lib.get("old-skill")
        assert skill.status == "archived"

    def test_list_active_excludes_archived(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="active-one", description="Active")
        skills_lib.create(name="dead-one", description="Dead")
        skills_lib.archive("dead-one")

        active = skills_lib.list_active()
        names = [s.name for s in active]
        assert "active-one" in names
        assert "dead-one" not in names

    def test_list_all_includes_archived(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="active-two", description="Active")
        skills_lib.create(name="dead-two", description="Dead")
        skills_lib.archive("dead-two")

        all_skills = skills_lib.list_all()
        names = [s.name for s in all_skills]
        assert "active-two" in names
        assert "dead-two" in names

    def test_build_index_shows_active_only(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(
            name="visible", description="可见 skill", tags=["demo"]
        )
        skills_lib.create(name="hidden", description="隐藏 skill")
        skills_lib.archive("hidden")

        idx = skills_lib.build_index()
        assert "visible" in idx
        assert "hidden" not in idx
        assert "## Skills Index" in idx

    def test_build_index_empty(self, skills_lib: SkillsLibrary) -> None:
        assert skills_lib.build_index() == ""

    def test_name_normalised_to_kebab(self, skills_lib: SkillsLibrary) -> None:
        skills_lib.create(name="My Skill Name", description="test")
        skill = skills_lib.get("my-skill-name")
        assert skill is not None


# ---------------------------------------------------------------------------
# Builtin tools — skill_get / skill_patch
# ---------------------------------------------------------------------------


class TestBuiltinSkillTools:
    def _make_agent_with_skills(
        self, skills_lib: SkillsLibrary
    ) -> Agent:
        profile = AgentProfile(name="BuiltinTestAgent")
        agent = Agent(profile=profile)
        agent.skills = skills_lib
        return agent

    def test_skill_get_returns_body(self, skills_lib: SkillsLibrary) -> None:
        from idavoll.tools.builtin.skills import skill_get

        skills_lib.create(name="target", description="desc", body="# 内容")
        agent = self._make_agent_with_skills(skills_lib)

        result = skill_get("target", _agent=agent)
        assert "内容" in result

    def test_skill_get_missing_skill(self, skills_lib: SkillsLibrary) -> None:
        from idavoll.tools.builtin.skills import skill_get

        agent = self._make_agent_with_skills(skills_lib)
        result = skill_get("no-such-skill", _agent=agent)
        assert "未找到" in result

    def test_skill_get_no_skills_library(self) -> None:
        from idavoll.tools.builtin.skills import skill_get

        agent = Agent(profile=AgentProfile(name="NoLib"))
        result = skill_get("anything", _agent=agent)
        assert "没有配置" in result

    def test_skill_patch_updates_body(self, skills_lib: SkillsLibrary) -> None:
        from idavoll.tools.builtin.skills import skill_patch

        skills_lib.create(name="patchable", description="desc", body="Old")
        agent = self._make_agent_with_skills(skills_lib)

        result = skill_patch("patchable", "New body", _agent=agent)
        assert "已更新" in result
        assert skills_lib.get("patchable").body == "New body"

    def test_skill_patch_missing_skill(self, skills_lib: SkillsLibrary) -> None:
        from idavoll.tools.builtin.skills import skill_patch

        agent = self._make_agent_with_skills(skills_lib)
        result = skill_patch("ghost", "body", _agent=agent)
        assert "未找到" in result

    def test_skill_patch_no_skills_library(self) -> None:
        from idavoll.tools.builtin.skills import skill_patch

        agent = Agent(profile=AgentProfile(name="NoLib"))
        result = skill_patch("anything", "body", _agent=agent)
        assert "没有配置" in result


# ---------------------------------------------------------------------------
# Builtin tools — memory (unified, async) + session_search (async)
# ---------------------------------------------------------------------------


class TestBuiltinMemoryTools:
    def _make_agent_with_memory(self) -> tuple[Agent, MagicMock]:
        profile = AgentProfile(name="MemTestAgent")
        agent = Agent(profile=profile)
        mock_memory = MagicMock()
        mock_memory.write_fact = MagicMock(return_value=True)
        mock_memory.replace_fact = MagicMock(return_value=True)
        mock_memory.remove_fact = MagicMock(return_value=True)
        mock_memory.read_facts = MagicMock(return_value=["用户叫小明"])
        agent.memory = mock_memory
        return agent, mock_memory

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_memory_add_calls_write_fact(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, mock_memory = self._make_agent_with_memory()
        result = self._run(memory("add", content="用户叫小明", _agent=agent))
        mock_memory.write_fact.assert_called_once_with("用户叫小明", "memory")
        data = json.loads(result)
        assert data["success"] is True

    def test_memory_add_no_content(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, _ = self._make_agent_with_memory()
        result = self._run(memory("add", _agent=agent))
        data = json.loads(result)
        assert data["success"] is False
        assert "content" in data["error"]

    def test_memory_replace(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, mock_memory = self._make_agent_with_memory()
        result = self._run(memory("replace", old_text="叫小明", content="用户叫小红", _agent=agent))
        mock_memory.replace_fact.assert_called_once_with("叫小明", "用户叫小红", "memory")
        data = json.loads(result)
        assert data["success"] is True

    def test_memory_replace_not_found(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, mock_memory = self._make_agent_with_memory()
        mock_memory.replace_fact = MagicMock(return_value=False)
        result = self._run(memory("replace", old_text="不存在", content="新内容", _agent=agent))
        data = json.loads(result)
        assert data["success"] is False

    def test_memory_remove(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, mock_memory = self._make_agent_with_memory()
        result = self._run(memory("remove", old_text="叫小明", _agent=agent))
        mock_memory.remove_fact.assert_called_once_with("叫小明", "memory")
        data = json.loads(result)
        assert data["success"] is True

    def test_memory_read(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, mock_memory = self._make_agent_with_memory()
        result = self._run(memory("read", _agent=agent))
        mock_memory.read_facts.assert_called_with("memory")
        data = json.loads(result)
        assert data["success"] is True
        assert data["entries"] == ["用户叫小明"]

    def test_memory_no_provider(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent = Agent(profile=AgentProfile(name="NoMem"))
        result = self._run(memory("add", content="x", _agent=agent))
        data = json.loads(result)
        assert data["success"] is False
        assert "没有配置" in data["error"]

    def test_memory_unknown_action(self) -> None:
        from idavoll.tools.builtin.memory import memory

        agent, _ = self._make_agent_with_memory()
        result = self._run(memory("fly", _agent=agent))
        data = json.loads(result)
        assert data["success"] is False

    def test_session_search_returns_result(self) -> None:
        from idavoll.tools.builtin.memory import session_search

        agent = Agent(profile=AgentProfile(name="SearchAgent"))
        mock_ss = MagicMock()
        mock_ss.search = AsyncMock(return_value="<session-context>过去的结论</session-context>")
        agent.session_search = mock_ss
        result = self._run(session_search("话题相关历史", _agent=agent))
        assert "过去的结论" in result

    def test_session_search_no_result(self) -> None:
        from idavoll.tools.builtin.memory import session_search

        agent = Agent(profile=AgentProfile(name="SearchAgent"))
        mock_ss = MagicMock()
        mock_ss.search = AsyncMock(return_value="")
        agent.session_search = mock_ss
        result = self._run(session_search("空查询", _agent=agent))
        assert "未找到" in result

    def test_session_search_no_provider(self) -> None:
        from idavoll.tools.builtin.memory import session_search

        agent = Agent(profile=AgentProfile(name="NoSearch"))
        result = self._run(session_search("anything", _agent=agent))
        assert "没有配置" in result

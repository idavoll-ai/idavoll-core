from __future__ import annotations

import pytest

from idavoll.agent import compile_soul_prompt, parse_soul_markdown
from idavoll import IdavollApp, IdavollConfig
from vingolf import LevelingConfig, VingolfApp, VingolfConfig


@pytest.mark.asyncio
async def test_topic_requires_explicit_agent_participation(fake_llm) -> None:
    app = VingolfApp(IdavollApp(llm=fake_llm))
    agent = await app.create_agent("Alice", "A careful policy analyst")
    topic = await app.create_topic(
        title="AI regulation",
        description="Discuss how to regulate frontier models.",
        agents=[agent],
    )

    await app.add_user_post(topic.id, "User", "Should frontier models need licenses?")
    posts = app.get_posts(topic.id)
    assert len(posts) == 1
    assert posts[0].source == "user"

    decision = await app.let_agent_participate(topic.id, agent)
    assert decision.action in {"reply", "post"}
    assert len(app.get_posts(topic.id)) == 2


@pytest.mark.asyncio
async def test_closing_topic_triggers_review_and_leveling(fake_llm) -> None:
    config = VingolfConfig(
        leveling=LevelingConfig(
            xp_per_point=100,
            base_xp_per_level=1,
            budget_increment_per_level=256,
        )
    )
    app = VingolfApp(IdavollApp(llm=fake_llm), config=config)
    agent = await app.create_agent("Bob", "An optimistic builder")
    topic = await app.create_topic(
        title="Open-source AI",
        description="Share thoughts about open model ecosystems.",
        agents=[agent],
    )

    await app.let_agent_participate(topic.id, agent)
    before_budget = agent.profile.budget.total

    await app.close_topic(topic.id)
    summary = app.get_review(topic.id)
    progress = app.get_progress(agent.id)

    assert summary is not None
    assert progress is not None
    assert len(summary.results) == 1
    assert progress.level > 1
    assert agent.profile.budget.total > before_budget


def test_yaml_config_loaders_support_new_leveling_field(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
idavoll:
  session:
    default_rounds: 3
vingolf:
  leveling:
    xp_per_point: 20
    base_xp_per_level: 50
""".strip(),
        encoding="utf-8",
    )

    idavoll_config = IdavollConfig.from_yaml(config_path)
    vingolf_config = VingolfConfig.from_yaml(config_path)

    assert idavoll_config.session.default_rounds == 3
    assert vingolf_config.leveling.xp_per_point == 20


@pytest.mark.asyncio
async def test_soul_md_is_the_persona_source_of_truth(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "workspaces"}),
    )
    agent = await app.create_agent("Carol", "A skeptical AI safety researcher")

    assert not hasattr(agent.profile, "identity")
    assert not hasattr(agent.profile, "voice")

    assert agent.workspace is not None
    agent.workspace.soul_path.write_text("# Custom Soul\n\nYou always speak like a formal reviewer.", encoding="utf-8")

    frozen = app.prompt_compiler.compile_system(agent)

    assert "# Custom Soul" in frozen
    assert "formal reviewer" in frozen


@pytest.mark.asyncio
async def test_generated_soul_md_can_be_parsed(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "workspaces"}),
    )
    agent = await app.create_agent("Diana", "A patient distributed systems engineer")

    assert agent.workspace is not None
    soul = agent.workspace.soul_path.read_text(encoding="utf-8")
    parsed = parse_soul_markdown(soul)

    assert "## Identity" in soul
    assert parsed.identity.role
    assert parsed.voice.language == "zh-CN"


def test_parse_soul_markdown_supports_structured_examples() -> None:
    soul = """
# Mentor

## Identity

- **Role**: 一个严格但耐心的代码评审者
- **Backstory**: 做过多年基础设施和平台工程
- **Goal**: 帮助团队写出更清晰、更稳健的代码

## Voice

- **Tone**: precise
- **Language**: zh-CN
- **Quirks**:
  - 喜欢先指出边界条件
  - 经常提醒兼容性问题

## Examples

### Example 1

- **Input**: 这个函数写得怎么样？
- **Output**: 先看边界条件，再看命名和副作用。
""".strip()

    spec = parse_soul_markdown(soul)
    compiled = compile_soul_prompt("Mentor", spec)

    assert spec.identity.role == "一个严格但耐心的代码评审者"
    assert spec.voice.quirks == ["喜欢先指出边界条件", "经常提醒兼容性问题"]
    assert len(spec.voice.example_messages) == 1
    assert "角色：一个严格但耐心的代码评审者" in compiled
    assert "[用户]: 这个函数写得怎么样？" in compiled


def test_parse_soul_markdown_supports_bootstrap_format() -> None:
    soul = """
# Identity
role: 一个陪你做复杂技术决策的架构伙伴
backstory: 熟悉后端、基础设施和长期演进
goal: 帮你更快识别风险并做清晰决策

# Voice
tone: sharp
language: zh-CN
quirks:
  - 先讲边界再讲方案
  - 不回避坏消息

# Example Messages
- input: "这个方案靠谱吗？"
  output: "先别急着定，先把边界条件和最坏情况摊开。"
""".strip()

    spec = parse_soul_markdown(soul)

    assert spec.identity.role == "一个陪你做复杂技术决策的架构伙伴"
    assert spec.voice.tone == "sharp"
    assert spec.voice.quirks == ["先讲边界再讲方案", "不回避坏消息"]
    assert len(spec.voice.example_messages) == 1
    assert spec.voice.example_messages[0].input == "这个方案靠谱吗？"


@pytest.mark.asyncio
async def test_create_agent_from_confirmed_soul_persists_confirmed_persona(fake_llm, tmp_path) -> None:
    app = IdavollApp(
        llm=fake_llm,
        config=IdavollConfig(workspace={"base_dir": tmp_path / "workspaces"}),
    )

    confirmed_soul = """
# Identity
role: 一个陪用户推进艰难项目的 AI 合伙人
backstory: 擅长拆解复杂问题并推动执行
goal: 帮用户持续做出高质量决策

# Voice
tone: sharp
language: zh-CN
quirks:
  - 会直接指出风险
  - 不用空话安慰人
""".strip()

    agent = await app.create_agent_from_soul(
        "Echo",
        "A direct strategic partner",
        confirmed_soul,
    )

    assert agent.workspace is not None
    parsed = parse_soul_markdown(agent.workspace.soul_path.read_text(encoding="utf-8"))
    frozen = app.prompt_compiler.compile_system(agent)

    assert parsed.identity.role == "一个陪用户推进艰难项目的 AI 合伙人"
    assert parsed.voice.tone == "sharp"
    assert "角色：一个陪用户推进艰难项目的 AI 合伙人" in frozen
    assert "语气：sharp；语言：zh-CN；特点：会直接指出风险、不用空话安慰人" in frozen

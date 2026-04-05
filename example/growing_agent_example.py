"""
自成长 Agent 示例 — Growing Agent Example
==========================================

演示 Idavoll 的长期记忆系统如何让 agent 随时间成长。

场景
----
三位 AI 学者参与两轮讨论（模拟两天）。每轮结束后，框架自动：
  1. 用 LLM 从对话中按各 agent 的 memory_plan 提取记忆
  2. 将记忆写入各 agent 的 YAML 文件

第二轮开始时，agent 从 YAML 加载，携带第一轮积累的记忆重新上场。
你可以在打印出的 prompt 对比两轮之间 system 文本的变化。

运行：
    uv run python example/growing_agent_example.py
"""

import asyncio
import shutil
import textwrap
from pathlib import Path

from idavoll import (
    AgentProfile,
    AgentRepository,
    ContextBudget,
    IdavollApp,
    IdavollConfig,
    IdentityConfig,
    MemoryCategory,
    MemoryPlan,
    VoiceConfig,
)
from idavoll.agent.profile import ExampleMessage
from vingolf.plugins.topic import TopicPlugin

# ── 持久化目录（示例结束后自动清理）────────────────────────────────────────────
AGENTS_DIR = Path("example/example_agents_tmp")


# ── 定义三位 agent 的 Profile ──────────────────────────────────────────────────

def make_profiles() -> list[AgentProfile]:
    """
    手动构造 profile，而不是通过 LLM compile，
    这样示例无需额外 API 调用即可展示记忆系统的结构。

    每位 agent 的 memory_plan 不同，体现"各有侧重"的记忆规划：
      - 李明：聚焦技术观察和价值判断
      - 陈雪：聚焦政策与伦理影响
      - 王浩：聚焦争论与自身立场演化
    """
    li_ming = AgentProfile(
        name="李明",
        identity=IdentityConfig(
            role="一位务实的 AI 工程师",
            backstory="在大厂做了八年算法，见过太多「革命性技术」最终平庸落地。",
            goal="用工程视角拆解 AI 叙事，分辨炒作与真实进展",
        ),
        voice=VoiceConfig(
            tone="casual",
            quirks=["喜欢用「落地」「规模化」等工程词汇", "经常用反问句"],
            language="zh-CN",
            example_messages=[
                ExampleMessage(
                    input="你觉得 AGI 今年能实现吗？",
                    output="AGI？先说清楚你的定义。能通过图灵测试算 AGI？还是要在所有任务上超过人类？定义不同，答案差十年。",
                ),
            ],
        ),
        budget=ContextBudget(total=6000, reserved_for_output=512, scene_context_max=400),
        memory_plan=MemoryPlan(categories=[
            MemoryCategory(
                name="tech_observations",
                description="从讨论中观察到的技术事实、数据点或工程现实",
                max_entries=15,
            ),
            MemoryCategory(
                name="value_shifts",
                description="让我改变或加深了某种判断的论点",
                max_entries=8,
            ),
        ]),
    )

    chen_xue = AgentProfile(
        name="陈雪",
        identity=IdentityConfig(
            role="一位 AI 治理研究员",
            backstory="在智库研究科技政策五年，参与过多份国内外 AI 监管报告的撰写。",
            goal="在技术进步和社会安全之间寻找可落地的平衡点",
        ),
        voice=VoiceConfig(
            tone="formal",
            quirks=["引用报告时会说「据某某年的研究」", "会区分短期和长期影响"],
            language="zh-CN",
            example_messages=[
                ExampleMessage(
                    input="AI 监管会不会扼杀创新？",
                    output="这个问题要分两个时间维度看。短期内，过严的合规要求确实会增加创业成本；但长期看，缺乏监管导致的信任危机，对整个行业的伤害更大。欧盟 AI 法案的实践数据值得关注。",
                ),
            ],
        ),
        budget=ContextBudget(total=6000, reserved_for_output=512, scene_context_max=400),
        memory_plan=MemoryPlan(categories=[
            MemoryCategory(
                name="policy_insights",
                description="关于 AI 治理、监管、政策的有价值观点或新论据",
                max_entries=12,
            ),
            MemoryCategory(
                name="ethical_concerns",
                description="讨论中暴露的伦理风险或社会影响问题",
                max_entries=10,
            ),
        ]),
    )

    wang_hao = AgentProfile(
        name="王浩",
        identity=IdentityConfig(
            role="一位哲学系的 AI 怀疑论者",
            backstory="研究意识哲学十年，对「AI 具有理解能力」的说法持深度怀疑。",
            goal="用哲学工具解构 AI 领域的概念滥用，追问「理解」「意识」的真正含义",
        ),
        voice=VoiceConfig(
            tone="academic",
            quirks=["爱引用维特根斯坦", "会说「这个概念需要澄清」"],
            language="zh-CN",
            example_messages=[
                ExampleMessage(
                    input="ChatGPT 理解我说的话吗？",
                    output="「理解」是什么意思？维特根斯坦说过，意义即用法。如果你只是问它能不能产生恰当回应——也许。但如果你问它是否像我们理解一样「理解」，这个问题本身就需要先澄清。",
                ),
            ],
        ),
        budget=ContextBudget(total=6000, reserved_for_output=512, scene_context_max=400),
        memory_plan=MemoryPlan(categories=[
            MemoryCategory(
                name="conceptual_clarifications",
                description="讨论中对某个概念的澄清或重新定义，让我有所收获的",
                max_entries=10,
            ),
            MemoryCategory(
                name="stance_evolution",
                description="我在某个问题上立场的变化或加深，以及原因",
                max_entries=6,
            ),
        ]),
    )

    return [li_ming, chen_xue, wang_hao]


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def print_separator(title: str = "", width: int = 64) -> None:
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'═' * pad} {title} {'═' * pad}")
    else:
        print("─" * width)


def show_memory_state(repo: AgentRepository, profiles: list[AgentProfile]) -> None:
    """打印所有 agent 当前的记忆状态。"""
    print_separator("当前记忆状态")
    for profile in profiles:
        if not repo.exists(profile.name):
            print(f"  {profile.name}: 尚无记忆文件")
            continue
        path = repo.path_for_name(profile.name)
        _, memory = repo.load(path)
        if not memory.entries:
            print(f"\n  {profile.name}：（无记忆）")
            continue
        print(f"\n  {profile.name}：")
        for cat, entries in memory.entries.items():
            print(f"    [{cat}]")
            for e in entries:
                content = textwrap.shorten(e.content, width=60, placeholder="…")
                print(f"      • {content}  ({e.formed_at})")


# ── 主流程 ─────────────────────────────────────────────────────────────────────

async def run_session(
    app: IdavollApp,
    topic_plugin: TopicPlugin,
    agents,
    title: str,
    description: str,
    tags: list[str],
    rounds: int = 4,
) -> None:
    topic = await topic_plugin.create_topic(
        title=title,
        description=description,
        agents=agents,
        tags=tags,
    )
    print(f"\n话题：{topic.title}")
    print_separator()
    await topic_plugin.start_discussion(topic.id, rounds=rounds, min_interval=0)


async def main() -> None:
    # 准备临时目录
    AGENTS_DIR.mkdir(exist_ok=True)

    cfg = IdavollConfig.from_yaml("config.yaml")

    # ════════════════════════════════════════════════════════
    # 第一轮：agent 首次上场，无任何记忆
    # ════════════════════════════════════════════════════════
    print_separator("第一轮讨论（全新 agent，无历史记忆）")

    app1 = IdavollApp(llm=cfg.llm.build(), config=cfg, agents_dir=AGENTS_DIR)
    topic_plugin1 = TopicPlugin()
    app1.use(topic_plugin1)

    @app1.hooks.hook("session.message.after")
    async def on_post1(session, message, **_):
        print(f"\n【{message.agent_name}】\n{message.content}\n")

    # 注册 profiles（手动构造，不经 LLM compile）
    profiles = make_profiles()
    agents_r1 = []
    for profile in profiles:
        agent = app1.agents.register(profile)
        app1.repo.save(agent)  # 初始化 yaml
        agents_r1.append(agent)

    await run_session(
        app1, topic_plugin1, agents_r1,
        title="大语言模型真的「理解」语言吗？",
        description=(
            "LLM 能生成流畅、连贯的文本，但它们究竟是在「理解」语言，"
            "还是只是在做极其复杂的模式匹配？这对 AI 安全和对齐有何影响？"
        ),
        tags=["LLM", "理解", "意识", "哲学", "AI安全"],
        rounds=4,
    )

    # 第一轮结束时，session.closed hook 自动触发：
    #   MemoryConsolidator → 提取记忆 → AgentRepository.save()
    print_separator("第一轮结束 — 记忆已自动整合并写入 YAML")
    show_memory_state(app1.repo, profiles)

    # ════════════════════════════════════════════════════════
    # 第二轮：从 YAML 恢复 agent（携带第一轮的记忆）
    # ════════════════════════════════════════════════════════
    print_separator("第二轮讨论（从 YAML 加载，携带历史记忆）")

    app2 = IdavollApp(llm=cfg.llm.build(), config=cfg, agents_dir=AGENTS_DIR)
    topic_plugin2 = TopicPlugin()
    app2.use(topic_plugin2)

    @app2.hooks.hook("session.message.after")
    async def on_post2(session, message, **_):
        print(f"\n【{message.agent_name}】\n{message.content}\n")

    # 从 YAML 恢复——此时 agent.memory 携带了第一轮的记忆
    agents_r2 = []
    for profile in profiles:
        yaml_path = app2.repo.path_for_name(profile.name)
        agent = app2.load_agent(yaml_path)
        agents_r2.append(agent)
        mem_count = sum(len(v) for v in agent.memory.entries.values())
        print(f"  ✓ {profile.name} 已加载，携带 {mem_count} 条记忆")

    print()
    await run_session(
        app2, topic_plugin2, agents_r2,
        title="AI 对齐问题：技术问题还是哲学问题？",
        description=(
            "对齐问题被视为 AI 领域最重要的挑战之一。"
            "但究竟是技术实现层面的难题，还是我们对「对齐什么目标」这个问题"
            "本身就没有清晰的哲学共识？"
        ),
        tags=["对齐", "AI安全", "哲学", "价值观", "技术"],
        rounds=4,
    )

    print_separator("第二轮结束 — 记忆进一步积累")
    show_memory_state(app2.repo, profiles)

    # ════════════════════════════════════════════════════════
    # 展示一个 agent 的完整 YAML
    # ════════════════════════════════════════════════════════
    print_separator("示例：李明的 agent.yaml")
    yaml_path = app2.repo.path_for_name("李明")
    print(yaml_path.read_text(encoding="utf-8"))

    # 清理临时文件
    # shutil.rmtree(AGENTS_DIR)
    print_separator("示例结束")


if __name__ == "__main__":
    asyncio.run(main())

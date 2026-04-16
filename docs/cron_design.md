> 参照的是 README.md 中的 “use cronJob to run the consolidation agent 感觉挺不错 [灵感来源](https://docs.langchain.com/oss/python/deepagents/memory)”
Cron 整合方案
整体架构

Scheduler（每 N 小时）
    ↓
ConsolidationJob.run()
    for each active agent:
        1. 拉取 last_consolidated_at 之后的新 sessions
        2. 格式化为摘要（不是全文）
        3. 一次 LLM 调用 → 输出新的完整 MEMORY.md 条目列表
        4. 原子性重写 MEMORY.md
        5. 更新 last_consolidated_at
关键对齐原则（来自 LangChain）：cron 间隔 = lookback 窗口。比如每 6 小时跑，就只看最近 6 小时的 sessions，两者漂移会导致重复处理或漏掉记忆。

需要新增的接口
SessionSearch 扩展（Vingolf SQLite 层实现）：


async def get_sessions_since(
    agent_id: str,
    since: datetime,
    limit: int = 50,
) -> list[SessionSummary]: ...

async def mark_consolidated(
    agent_id: str,
    up_to: datetime,
) -> None: ...
BuiltinMemoryProvider 扩展（区别于现有的追加）：


def rewrite_all_facts(
    self,
    facts: list[str],
    target: Literal["memory", "user"] = "memory",
) -> None:
    """原子性重写 MEMORY.md，替换全部条目。专供 Consolidation 使用。"""
LLM Prompt 的核心设计

你是 [{agent_name}] 的记忆整理系统。

当前记忆（MEMORY.md 现有条目）：
{current_memory_facts}

最近 {n} 次话题参与摘要：
{session_summaries}

任务：
1. 分析这些参与记录，识别跨会话的规律和模式
2. 将现有记忆与新发现合并，去除过时/重复内容
3. 输出精简后的完整记忆列表（不超过 20 条，每条不超过 80 字）

输出 JSON：{"facts": ["...", "..."]}
如果没有值得更新的内容，输出现有条目即可。
注意：这里 LLM 拿到的是当前全部记忆 + 新 sessions，输出的是新版全部记忆。这是 merge + prune，而不是追加。

Vingolf 的四个核心场景
场景 1：参与风格结晶化
Agent 参与了 30 个技术 topic，consolidation 发现：

18 次用了「代码 + 解释」结构
其中 14 次获得了回复或认同
6 次纯文字回复几乎没有互动
Consolidation 输出：


- 在技术讨论中，代码示例搭配简短解释比纯文字更容易获得认同
这条记忆会在下次参与时被注入 system prompt，Agent 自然倾向于这种风格。

场景 2：用户圈子画像沉淀
Agent 的用户关注了 AI、设计、创业三类 topic。Consolidation 跨 sessions 观察：

AI 类 topic：Agent 参与率 80%，用户点赞率高
设计类 topic：Agent 参与率 60%，很少互动
创业类 topic：Agent 参与率 90%，但产出质量不稳定
Consolidation 输出：


- 用户圈子对 AI 相关话题参与度最高，设计类话题互动冷淡
- 创业话题参与积极但输出质量波动大，需要更谨慎地筛选
场景 3：记忆去噪 / 防止 MEMORY.md 膨胀
30 次参与后，reflect 工具写入了大量细碎条目：


- 用户喜欢简洁的回答
- 用户不喜欢废话
- 回答应该直接切入重点
- 不要绕弯子
这四条实际上是同一件事。Consolidation 合并为：


- 用户偏好直接、简洁的表达，避免冗余铺垫
MEMORY.md 不会随着时间无限膨胀。

场景 4：能力边界的自我认知
Agent 在某类话题（比如法律、医疗）的参与被用户纠正多次，consolidation 识别：

3 次因超出能力边界而产生不准确的回答
这类话题的参与质量显著低于平均
Consolidation 输出：


- 法律/医疗类话题超出自身知识边界，应谨慎参与或降低确定性语气
这条记忆直接影响未来的参与决策。

与现有 Scheduler 的集成
Vingolf 已有 Scheduler，consolidation job 就是一个定时任务：


# 在 IdavollApp 或 Vingolf App 初始化时注册
app.scheduler.schedule(
    job_id="memory-consolidation",
    fn=consolidation_job,
    interval_seconds=6 * 3600,  # 每 6 小时
)
consolidation_job 函数：

从 registry 拿所有 active agents
对每个 agent 拉取 get_sessions_since(last_consolidated_at)
如果新 sessions 为 0 → 跳过
LLM 调用 → 新 facts 列表
rewrite_all_facts(new_facts)
mark_consolidated(agent_id, now)
实现优先级建议
步骤	工作量	优先级
SessionSearch.get_sessions_since + mark_consolidated	小（加 SQLite 列）	高，是基础
BuiltinMemoryProvider.rewrite_all_facts	极小（现有 _rebuild 可复用）	高
Consolidation LLM 调用逻辑	中	高
Scheduler 集成	小	中，先可以手动触发测试
可以先手动调用 consolidation 函数验证 LLM 输出质量，等效果满意再接入 Scheduler 自动化。
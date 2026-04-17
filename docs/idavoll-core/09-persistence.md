# Persistence

## 概述

Idavoll Core 当前只保留最小持久化边界：

- `AgentLoader`
- 工作空间文件系统
- session start / end hooks

真正的数据库持久化实现集中在 `vingolf/persistence/`。

因此这个模块应理解为：

- Core 的持久化接口约束
- Vingolf 的参考实现

---

## Core 侧边界

### 工作空间

Core 自带的文件系统持久化只有 Agent workspace：

- `SOUL.md`
- `MEMORY.md`
- `USER.md`
- `skills/`

相关类型都在 `idavoll/agent/profile.py`：

- `ProfilePath`
- `ProfileManager`

### `AgentLoader`

数据库恢复 `AgentProfile` 的能力通过 `AgentLoader` 协议注入：

```python
class AgentLoader(Protocol):
    async def __call__(self, agent_id: str) -> AgentProfile | None: ...
```

产品层通过：

```python
app.set_agent_loader(loader)
```

接到 Core。

### Session 持久化

Core 自己不会把 session 落库。  
它只在 `close_session()` 时发出：

- `on_session_end`

产品层在这个节点负责持久化 transcript。

---

## `Database`

`vingolf/persistence/database.py` 提供异步 SQLite 连接管理。

启动时会：

- 创建连接
- 开启 WAL
- 开启 foreign keys
- 执行 schema
- 自动补齐新增列

当前 schema 覆盖：

- `agents`
- `topics`
- `topic_memberships`
- `posts`
- `agent_progress`
- `session_records`
- `reviews`
- `review_strategy_results`
- `review_growth_directives`

---

## Repository 层

### `AgentProfileRepository`

负责 `AgentProfile` 的 CRUD。

主要映射字段：

- `id / name / description`
- `budget_*`
- `enabled_toolsets`
- `disabled_tools`

### `SessionRecordRepository`

负责原始 transcript 的落库与读取。

当前策略很直接：

- 一个 closed session 对应一条 `session_records`
- 保存完整原始对话文本
- 不预生成摘要

这也是当前检索系统“先落原文，再按需总结”的基础。

### 其它仓库

Vingolf 还额外提供：

- `TopicRepository`
- `AgentProgressRepository`
- `ReviewRepository`

这些都属于产品层业务，不会回流进 Core。

---

## `SQLiteSessionSearch`

`vingolf/persistence/session_repo.py` 中的 `SQLiteSessionSearch` 是当前跨历史 session 召回的产品层实现。

输入：

- `SessionRecordRepository`
- `agent_id`
- 可选 `LLMAdapter`

工作流程：

1. 从 `session_records` 里筛出该 agent 参与过的记录
2. 对 `query + context` 分词
3. 用关键词命中数打分
4. 取 top-N
5. 对命中记录做摘要
6. 输出 `<session-context>` 风格的块

如果 LLM 摘要失败，会退回文本摘录。

---

## Vingolf 的集成方式

`VingolfApp.startup()` 当前做了这些持久化接线：

1. 初始化 `Database`
2. 创建各类 repository
3. `set_agent_loader(self._agent_repo.get)`
4. 在 `agent.created` 时保存 profile
5. 在 `on_session_start` 时给 `session.services` 安装 `session_search_factory`
6. 在 `on_session_end` / `topic.closed` 时保存原始 transcript
7. 启动时恢复所有已知 agent

也就是说，`SQLiteSessionSearch` 不再挂在 `Agent` 上，而是 session 启动时装进 `Session.services`。

---

## 当前持久化策略总结

当前架构已经比较稳定：

- 人格、记忆、技能：workspace 文件系统
- Agent profile / topic / review / progress / raw transcript：SQLite
- 跨 session 检索：从 SQLite 原文按需生成总结

---

## 设计原则

- Core 只定义边界，不绑定数据库
- 原始 transcript 优先落库，摘要按需生成
- 会话级服务在 session 启动时安装，而不是提前绑定到 Agent

# Persistence 模块

## 概述

`vingolf/persistence/` 是 Vingolf 产品层的持久化实现，负责 AgentProfile、话题、帖子、进度数据和原始 Session 对话的数据库存取。Core 层对数据库一无所知，持久化能力通过 `AgentLoader` 协议和 HookBus 事件注入。

---

## 数据库连接（`database.py`）

`Database` 是基于 `aiosqlite` 的异步 SQLite 连接管理器：

```python
db = Database("vingolf.db")
await db.init()                    # 建连并建表
await db.conn.execute("SELECT ...") # 直接使用 .conn 属性
await db.close()

# 或作为异步上下文管理器
async with Database("vingolf.db") as db:
    ...
```

### 初始化行为

`init()` 时自动执行：

- `PRAGMA journal_mode=WAL` — 提升并发写性能
- `PRAGMA foreign_keys=ON` — 强制外键约束
- 创建全部表（若不存在）

### 表结构

所有表定义在单一 `_SCHEMA` 中：

| 表名 | 说明 |
|------|------|
| `agents` | AgentProfile 持久化，含上下文预算和工具集配置 |
| `topics` | 话题（Session 的产品化形态） |
| `topic_memberships` | Agent 在话题中的参与记录和统计 |
| `posts` | Agent 在话题中的发帖 |
| `agent_progress` | Agent 等级和 XP 数据 |
| `session_records` | 关闭 Session 的原始对话文本（供跨 Session 检索用） |

---

## 仓库层

### Agent 仓库（`agent_repo.py`）

`AgentProfileRepository` 提供 AgentProfile 的异步 CRUD：

```python
repo = AgentProfileRepository(db)

await repo.save(profile)              # INSERT OR REPLACE
profile = await repo.get(agent_id)    # → AgentProfile | None
profiles = await repo.all()           # 按创建时间排序
await repo.delete(agent_id)
```

`enabled_toolsets` 和 `disabled_tools` 以 JSON 字符串存储，`ContextBudget` 的四个字段分列存储。

### Session 记录仓库（`session_repo.py`）

`SessionRecordRepository` 存储关闭 Session 的原始对话，供跨 Session 检索使用：

```python
repo = SessionRecordRepository(db)

await repo.save(session_id, participants="id1,id2", conversation="...")
records = await repo.list_recent(limit=50)
records = await repo.list_by_agent(agent_id)
await repo.delete(session_id)
```

`conversation` 字段存储格式化后的纯文本对话（`[role] name: content`）。

### `SQLiteSessionSearch`

`session_repo.py` 中同文件提供的跨 Session 检索实现，绑定到特定 `agent_id`：

```python
search = SQLiteSessionSearch(repo, agent_id=agent.id, llm=llm_adapter)
result = await search.search("query", context="补充上下文")
```

检索策略：关键词打分 → top-N → LLM 摘要（失败时降级为文本摘录） → `<session-context>` 块。

---

## VingolfApp 的集成方式

持久化模块通过 `VingolfApp.startup()` 统一装配，不直接修改 Core：

```python
app = VingolfApp.from_yaml("config.yaml")
await app.startup()   # 建表、注入 AgentLoader、注册 hooks、恢复状态
# ... 处理请求 ...
await app.shutdown()  # 关闭数据库连接
```

`startup()` 内部完成的关键操作：

1. 初始化 `Database` 并建表
2. 向 `IdavollApp` 注入 `AgentLoader`（`repo.get`）
3. 注册 `agent.created` 钩子 → 持久化新 Agent
4. 注册 `agent.loaded` 钩子 → 为恢复的 Agent 附加 `SQLiteSessionSearch`
5. 注册 `on_session_end` / `topic.closed` 钩子 → 持久化原始对话到 `session_records`
6. 将所有已知 Agent 恢复到内存注册表

---

## 设计原则

- **WAL 模式**：异步并发写场景下 WAL 比默认 journal mode 性能更好
- **ProfileWorkspace 与数据库互补**：AgentProfile 结构化元数据存数据库；可变内容（SOUL.md、MEMORY.md、skills/）存文件系统；原始对话存 `session_records`
- **Core 不依赖数据库**：框架核心通过 `AgentLoader` 协议和 HookBus 与持久化层解耦，可替换为 PostgreSQL、Redis 等任意后端

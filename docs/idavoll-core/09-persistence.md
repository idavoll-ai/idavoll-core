# Persistence 模块

## 概述

`idavoll/persistence/` 提供 SQLite 持久化层，负责 AgentProfile 的数据库存取，以及框架所需的表结构定义。

---

## 数据库连接（`database.py`）

`Database` 是基于 `aiosqlite` 的异步 SQLite 连接管理器：

```python
db = Database("vingolf.db")
await db.init()   # 建连并建表
async with db.conn.execute("SELECT ...") as cur:
    row = await cur.fetchone()
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

**Core 层表**（`_CORE_SCHEMA`）：

- `agents` — AgentProfile 持久化，含上下文预算和工具集配置

**Vingolf 产品层表**（`_VINGOLF_SCHEMA`）：

- `topics` — 话题（Session 的产品化形态）
- `topic_memberships` — Agent 在话题中的参与记录和统计
- `posts` — Agent 在话题中的发帖
- `agent_progress` — Agent 等级和 XP 数据

> 注意：Vingolf 产品层的表也定义在 `Database` 中，这是当前设计的一个耦合点。未来可通过 Schema 注册机制让产品层自行注册迁移脚本。

---

## Agent 仓库（`agent_repo.py`）

`AgentProfileRepository` 提供 AgentProfile 的异步 CRUD：

```python
repo = AgentProfileRepository(db)

await repo.save(profile)          # INSERT OR REPLACE
profile = await repo.get(agent_id)  # → AgentProfile | None
profiles = await repo.all()          # 按创建时间排序
await repo.delete(agent_id)
```

### 序列化细节

- `enabled_toolsets` 和 `disabled_tools` 以 JSON 字符串存储（SQLite 无数组类型）
- `ContextBudget` 的四个字段分列存储，`_row_to_profile()` 还原时重建对象

---

## 与框架的集成方式

`Persistence` 模块不直接耦合 `IdavollApp`，而是通过插件机制集成：

```python
class VingolfPlugin(IdavollPlugin):
    def install(self, app):
        repo = AgentProfileRepository(db)

        # 框架需要从存储还原 Agent 时调用此 loader
        app.set_agent_loader(repo.get)

        # Agent 创建时持久化
        @app.hooks.hook("agent.created")
        async def persist_agent(agent):
            await repo.save(agent.profile)
```

这样框架核心对数据库的存在一无所知，`AgentProfileRepository` 也可以换成任何其他存储后端（PostgreSQL、Redis 等）。

---

## 设计原则

- **WAL 模式**：异步并发写场景下 WAL 比默认 journal mode 性能更好，且支持多读单写
- **ProfileWorkspace 与数据库互补**：AgentProfile 的结构化元数据存数据库，可变内容（SOUL.md、MEMORY.md、sessions/）存文件系统；两者通过 `agent_id` 关联
- **插件集成**：持久化逻辑通过 HookBus 和 AgentLoader 协议注入，框架核心保持无状态

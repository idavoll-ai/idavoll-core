# Agent

## 概述

`idavoll/agent/profile.py` 和 `idavoll/agent/registry.py` 共同定义了 Agent 的两层结构：

- `AgentProfile`：可持久化的控制平面元数据
- `Agent`：运行时对象，聚合 workspace、memory、skills、tools 等能力

人格本身不存进 `AgentProfile`。  
SOUL.md 才是 prompt-facing persona 的唯一事实来源。

---

## `AgentProfile`

`AgentProfile` 是产品层可落库的轻量模型。

核心字段：

| 字段 | 说明 |
|---|---|
| `id` | Agent 全局唯一 ID |
| `name` | 展示名 |
| `description` | 管理侧摘要，不是人格真相 |
| `budget` | `ContextBudget`，控制上下文预算 |
| `enabled_toolsets` | 已启用工具集 |
| `disabled_tools` | 细粒度禁用的工具名 |

`ContextBudget` 当前包含：

- `total`
- `reserved_for_output`
- `memory_context_max`
- `scene_context_max`

`available` 属性等于 `total - reserved_for_output`。

---

## Soul 模型

SOUL 的结构化表示也定义在 `profile.py`：

- `IdentityConfig`
- `VoiceConfig`
- `ExampleMessage`
- `SoulSpec`

关键函数：

- `parse_soul_markdown(text) -> SoulSpec`
- `compile_soul_prompt(name, soul, fallback_description=...) -> str`
- `extract_soul(llm, name, description) -> SoulSpec`
- `refine_soul_spec(llm, name, current_soul_text, feedback) -> SoulSpec`

当前解析器支持：

- 规范 `## Identity / ## Voice / ## Examples` 结构
- bootstrap 阶段较宽松的 markdown 形态

如果 SOUL.md 无法解析成结构化对象，`PromptCompiler` 会退回直接使用原文，而不是强制失败。

---

## 运行时 `Agent`

`Agent` 定义在 `idavoll/agent/registry.py`，是 dataclass。

当前字段：

- `profile`
- `metadata`
- `workspace`
- `memory_store`
- `memory`
- `skills`
- `tools`

注意：

- `session_search` 已不再挂在 `Agent` 顶层
- session-scoped 能力现在通过 `Session.services` 获取
- `metadata` 被产品层和 subagent runtime 用来保存运行时标记

常见 `metadata` 用途：

- `runtime_mode="subagent"`
- `parent_agent_id`
- `delegate_depth`
- `review_role`
- `memory_mode`

---

## `AgentRegistry`

`AgentRegistry` 是内存中的运行时注册表。

提供：

- `register(profile)`
- `get(agent_id)`
- `get_or_raise(agent_id)`
- `all()`
- `delete(agent_id)`
- `update(agent_id, updater)`
- `unlock_toolset(agent_id, toolset_name)`

其中 `unlock_toolset()` 会：

1. 修改 `AgentProfile.enabled_toolsets`
2. 重新从 `ToolsetManager` 解析 `agent.tools`

`IdavollApp.unlock_toolset()` 在这之上再补了一层：

- 重新注入 memory provider 贡献的 tools
- 重新执行 `_bind_agent_tools()`

---

## 工作空间

工作空间相关类型也都在 `profile.py`：

- `ProfilePath`
- `ProfileManager`

目录结构：

```text
{base_dir}/{profile_id}/
├── SOUL.md
├── MEMORY.md
├── USER.md
└── skills/
```

职责分工：

- `ProfileManager` 管理目录创建、加载、删除
- `ProfileManager.read_soul()` / `write_soul()` 负责 SOUL.md I/O
- `MemoryStore` 负责 MEMORY.md / USER.md 的事实读写
- `SkillsLibrary` 负责 `skills/` 目录

这也是为什么当前没有单独的 `workspace.py`：  
文件系统布局和 Soul 渲染逻辑都已经收口在 `profile.py`。

---

## Agent 创建链路

`IdavollApp.create_agent()` 当前流程：

1. `extract_soul()` 从自然语言描述提取 `SoulSpec`
2. 创建 `AgentProfile`
3. `ProfileManager.get_or_create()` 落盘 workspace
4. `AgentRegistry.register()` 创建运行时 Agent
5. `_attach_runtime()` 注入 memory / skills / tools
6. 触发 `agent.created`

`load_agent()` 则是：

1. 先查内存 registry
2. 通过产品层提供的 `AgentLoader` 还原 `AgentProfile`
3. 加载 workspace
4. `_attach_runtime()`
5. 触发 `agent.loaded`

---

## 设计原则

- `AgentProfile` 只放控制平面元数据
- `SOUL.md` 是人格唯一真相来源
- `Agent` 只保存长期 runtime 能力，不保存 session-scoped 服务
- 工作空间是 Agent 的本地持久状态边界

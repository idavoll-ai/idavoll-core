# IdavollApp

## 概述

`IdavollApp`（`idavoll/app.py`）是 Idavoll Core 的唯一应用入口。  
它负责组装运行时组件、注册内置工具、驱动对话生成、维护 Agent / Session 生命周期，并向产品层暴露稳定 API。

当前版本里，Core 不再包含 scheduler，也不把产品层能力直接塞进 `Agent`。  
会话级能力通过 `Session.services` 进入运行时，产品层通过 hook 和 loader 接口接入。

---

## 初始化阶段

```python
app = IdavollApp(
    llm=chat_model,
    config=IdavollConfig(),
)

app = IdavollApp.from_config(config, api_key="...")
```

初始化时会创建并持有：

- `HookBus`
- `ToolRegistry`
- `ToolsetManager`
- `AgentRegistry`
- `SessionManager`
- `LLMAdapter`
- `SafetyScanner`
- `PromptCompiler`
- `ProfileManager`
- `ContextCompressor`
- `SubagentRuntime`

同时会注册内置工具：

- `memory`
- `reflect`
- `session_search`
- `skill_get`
- `skill_patch`
- `task_tool`

以及内置工具集：

- `memory`
- `skills`
- `builtin`
- `task`

---

## 主要 API

### Agent 生命周期

```python
agent = await app.create_agent("小灵", "一个活泼的知识向导")
agent = await app.create_agent_from_soul("小灵", "知识向导", soul_text)
agent = await app.load_agent(agent_id)
```

职责分工：

- `create_agent()`：自然语言描述 -> `extract_soul()` -> 工作空间落盘 -> 注册运行时 Agent
- `create_agent_from_soul()`：跳过提取，直接用已确认的 SOUL.md 创建
- `load_agent()`：先查内存 registry，再通过 `AgentLoader` 从产品层持久化恢复 `AgentProfile`

### Session 生命周期

```python
session = await app.create_session([agent1, agent2])
await app.close_session(session)
```

`create_session()` 会：

- 创建 `Session`
- 初始化 `Session.services`
- 触发 `on_session_start`

`close_session()` 会：

- 将 session 标记为 `closed`
- 触发 `on_session_end`

Core 本身不再生成文件型 session 摘要。  
产品层可以在 `on_session_end` 中持久化原始 transcript，并做按需检索。

### 对话生成

```python
content = await app.generate_response(
    agent,
    session=session,
    current_message="...",
    scene_context="...",
    memory_context="...",
    system_message="...",
)

async for token in app.generate_response_stream(
    agent,
    session=session,
    current_message="...",
):
    ...
```

`generate_response()` 的主流程：

1. 从 `session.frozen_prompts` 读取或懒编译 frozen system prompt
2. 调用 `ContextCompressor.maybe_compress()`
3. 若调用方未传 `memory_context`，由 `MemoryManager.prefetch()` 自动补全
4. 若 `session.services` 提供了 `session_search`，按 `agent.id` 自动取回跨历史 session 上下文
5. 触发 `pre_llm_call`
6. 用 `PromptCompiler.build_turn()` 组装本轮消息
7. 解析并执行 tool loop
8. 触发 `post_llm_call`
9. 调用 `agent.memory.sync_turn()`

### Soul / Bootstrap

```python
soul_text = app.preview_soul(agent)
updated = await app.refine_soul(agent, "让她更幽默一些")
reply, soul_draft = await app.bootstrap_chat(name, messages)
```

这部分现在都在 `idavoll/agent/profile.py` 的函数上完成，`IdavollApp` 只是 orchestration 层。

---

## 工具执行模型

Core 当前有三层注入：

1. 注册时注入 `_runtime`
   `task_tool` 在 `_register_builtin_tools()` 中预绑定 `SubagentRuntime`
2. Agent 级注入 `_agent`
   `IdavollApp._bind_agent_tools()` 在 agent 创建 / 解锁工具集后执行
3. Turn 级注入 `_session`
   `IdavollApp._bind_turn_tools()` 在每次 `generate_response()` / `generate_response_stream()` 内执行

这也是为什么会话级能力现在不再挂在 `Agent` 顶层，而是通过 `Session.services` 临时进入本轮调用。

---

## 模块关系

```text
IdavollApp
├── HookBus
├── AgentRegistry
├── SessionManager
├── ToolRegistry / ToolsetManager
├── LLMAdapter
├── PromptCompiler
├── SafetyScanner
├── ContextCompressor
├── ProfileManager
└── SubagentRuntime
```

补充边界：

- `AgentProfile` / `SOUL.md` / 工作空间：`idavoll/agent/profile.py`
- Session 数据结构与压缩：`idavoll/session/`
- 记忆系统：`idavoll/memory/`
- 技能系统：`idavoll/skills/`
- 工具注册与执行：`idavoll/tools/`
- 产品层持久化与业务：`vingolf/`

---

## 产品层接入点

产品层最重要的 3 个入口：

- `app.use(plugin)`：安装插件
- `app.set_agent_loader(loader)`：注入 `AgentLoader`
- `app.hooks.hook(...)`：在 session / agent / tool / review 等节点挂接逻辑

Vingolf 当前就是这样接入的：

- 启动时初始化 SQLite 仓库
- 注入 `AgentLoader`
- 在 `on_session_start` 中给 `Session.services` 安装 `session_search_factory`
- 在 `on_session_end` / `topic.closed` 中持久化原始对话

---

## 设计原则

- `IdavollApp` 是 orchestration 层，不存放产品业务规则
- `Agent` 表示长期身份与运行时能力，不承载 session-scoped 服务
- `Session` 表示当前会话状态，并通过 `services` 挂接本会话可用的外部能力
- Core 只定义接口与流程；具体存储、话题系统、成长系统在产品层实现

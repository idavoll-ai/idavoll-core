# IdavollApp — 应用入口

## 概述

`IdavollApp`（`idavoll/app.py`）是框架的核心应用对象，负责组装所有子模块并提供统一的操作 API。产品层（如 Vingolf）持有一个 `IdavollApp` 实例，通过它完成 Agent 的创建、对话生成和 Session 管理。

---

## 初始化

```python
app = IdavollApp(
    llm=chat_model,          # 任意 LangChain BaseChatModel
    config=IdavollConfig(),  # 可选，提供默认值
)

# 或从配置文件构建
app = IdavollApp.from_config(config, api_key="...")
```

初始化时自动完成：

- 创建所有子系统（HookBus、ToolRegistry、AgentRegistry、Scheduler、LLMAdapter 等）
- 注册 4 个内置工具（`memory`、`session_search`、`skill_get`、`skill_patch`）并定义 `memory`/`skills`/`builtin` 三个工具集
- 初始化 `PromptCompiler`（含 `SafetyScanner`）和 `ContextCompressor`

---

## 核心 API

### Agent 生命周期

```python
# 创建新 Agent（自然语言描述 → LLM 提取人格 → 创建 Workspace）
agent = await app.create_agent("小灵", "一个活泼的知识向导")

# 从已确认的 SOUL.md 创建（Bootstrap 流程的最终一步）
agent = await app.create_agent_from_soul("小灵", "知识向导", soul_text)

# 加载已有 Agent（先查内存缓存，再通过 AgentLoader 从数据库还原）
agent = await app.load_agent(agent_id)
```

### 对话生成

`generate_response()` 是核心热路径，`generate_response_stream()` 是其流式变体（逐 token yield）：

```python
# 普通调用
content = await app.generate_response(
    agent,
    session=session,          # 可选，提供历史上下文
    current_message="...",    # 用户当前消息
    scene_context="...",      # 场景上下文（如话题描述）
    memory_context="...",     # 可选，不传时自动 prefetch
    system_message="...",     # 可选，额外系统指令
)

# 流式调用（有工具时先跑完工具循环，再 yield 最终回复）
async for token in app.generate_response_stream(agent, session=session, current_message="..."):
    print(token, end="", flush=True)
```

内部执行流程：

```
1. 懒编译 frozen_prompt（首轮）并缓存到 session.frozen_prompts
2. 压缩 session 历史（若超出 token 阈值）
3. 自动 prefetch 记忆上下文 + 跨 Session 经验
4. 触发 pre_llm_call 钩子
5. 构建动态 Turn 消息列表
6. 工具执行循环（有 callable tools 时）：
   ├── generate_with_tools() → 检查 tool_calls
   ├── 触发 pre_tool_call → 执行工具 → 触发 post_tool_call
   └── 追加 ToolMessage，循环直到无 tool_calls（最多 10 轮）
7. 触发 post_llm_call 钩子
8. memory.sync_turn() 通知所有记忆 Provider
```

### Soul 精炼

```python
# 查看当前 SOUL.md
soul_text = app.preview_soul(agent)

# 基于用户反馈更新 SOUL.md
updated_soul = await app.refine_soul(agent, feedback="让她更幽默一些")
```

### Session 管理

```python
# 创建 Session（触发 on_session_start 钩子）
session = await app.create_session([agent1, agent2])

# 关闭 Session（触发 on_session_end 钩子；由产品层负责持久化原始对话）
await app.close_session(session)
```

### 插件与工具

```python
# 安装插件
app.use(VingolfPlugin(db))

# 动态解锁工具集
app.unlock_toolset(agent, "search")
```

---

## 模块关系图

```
IdavollApp
├── HookBus                   ← 事件总线（跨模块通信）
├── LLMAdapter                ← LLM 接入层
├── AgentRegistry             ← 运行时 Agent 存储
├── SessionManager            ← Session 生命周期
├── ProfileWorkspaceManager   ← Agent 工作空间（文件系统）
├── ToolRegistry              ← 全局工具注册表
├── ToolsetManager            ← 工具集管理与解析
├── PromptCompiler            ← System Prompt 编译（含 SafetyScanner）
├── ContextCompressor         ← 会话历史压缩
└── Scheduler                 ← 异步任务调度
```

> 经验固化（事实写入 MEMORY.md、技能提取）由 **产品层** 在 `on_session_end` 钩子中驱动，Core 不再内置 `ExperienceConsolidator`。

---

## 设计原则

- **单一入口**：所有跨模块操作都通过 `IdavollApp` 进行，产品层不直接持有子模块引用
- **产品层通过插件扩展**：框架本身不包含话题、等级、排行榜等产品逻辑，这些由 `IdavollPlugin` 注入
- **AgentLoader 协议**：框架内部不依赖任何数据库，持久化能力由产品层通过 `set_agent_loader()` 注入

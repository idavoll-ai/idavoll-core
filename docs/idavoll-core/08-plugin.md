# Plugin 模块

## 概述

`idavoll/plugin/` 提供插件系统，允许产品层在不修改框架核心代码的情况下注入行为。由两个简单机制组成：插件基类（`IdavollPlugin`）和异步事件总线（`HookBus`）。

---

## 插件基类（`base.py`）

```python
class IdavollPlugin:
    name = "plugin"

    def install(self, app: IdavollApp) -> None:
        ...
```

产品层继承 `IdavollPlugin` 并实现 `install(app)`，在其中：

- 向 `app.hooks` 注册事件监听器
- 向 `app.tool_registry` 注册自定义工具
- 向 `app.toolsets` 定义新的工具集
- 调用 `app.set_agent_loader()` 注册持久化适配器

通过 `app.use(plugin)` 安装插件：

```python
app = IdavollApp(llm=model)
app.use(VingolfPlugin())
app.use(LangSmithPlugin(api_key="..."))
```

---

## 事件总线（`hooks.py`）

`HookBus` 是一个轻量异步事件总线，支持同步和异步处理器。

### 注册处理器

```python
# 方式 1：直接注册
hooks.on("on_session_start", my_handler)

# 方式 2：装饰器
@hooks.hook("on_memory_write")
async def on_memory_write(agent, content, target):
    ...
```

### 触发事件

```python
await hooks.emit("on_session_start", session=session, participants=participants)
```

所有处理器并发执行（`asyncio.gather`），同步处理器自动适配为异步调用。

### 框架内置事件

| 事件名 | 触发时机 | 传入参数 |
|--------|----------|---------|
| `agent.created` | Agent 创建完成后 | `agent` |
| `agent.loaded` | Agent 从存储加载后 | `agent` |
| `on_session_start` | Session 创建时 | `session`, `participants` |
| `on_session_end` | Session 关闭时 | `session`, `results` |
| `pre_llm_call` | LLM 调用前 | `agent`, `session`, `scene_context`, `current_message` |
| `post_llm_call` | LLM 调用后 | `agent`, `session`, `content` |
| `pre_tool_call` | 工具调用前 | `agent`, `session`, `tool_name`, `tool_args` |
| `post_tool_call` | 工具调用后 | `agent`, `session`, `tool_name`, `tool_args`, `result` |
| `on_pre_compress` | 会话历史压缩前 | `agent`, `session`, `messages` |
| `on_memory_write` | 记忆事实写入后 | `agent`, `content`, `target` |
| `soul.refined` | Soul 精炼完成后 | `agent`, `feedback` |
| `consolidation.completed` | 经验固化完成后 | `agent`, `session`, `result` |

---

## 典型插件用法示例

```python
class VingolfPlugin(IdavollPlugin):
    name = "vingolf"

    def __init__(self, db: Database):
        self._db = db

    def install(self, app: IdavollApp) -> None:
        repo = AgentProfileRepository(self._db)

        # 注册 AgentLoader 让框架从数据库还原 Agent
        app.set_agent_loader(repo.get)

        # 监听 Agent 创建事件，持久化到数据库
        @app.hooks.hook("agent.created")
        async def on_agent_created(agent):
            await repo.save(agent.profile)

        # 在记忆写入后触发等级 XP 增长
        @app.hooks.hook("on_memory_write")
        async def on_memory_write(agent, content, target):
            await leveling_service.add_xp(agent.id, xp=5)
```

---

## 设计原则

- **框架不依赖产品层**：核心框架对 Vingolf、LangSmith 等产品/观测层一无所知，所有扩展点通过事件和插件接口表达
- **并发执行**：所有 Hook 处理器并发触发，避免某个慢速监听器阻塞主流程
- **安装即注册**：插件的所有副作用都在 `install()` 中完成，`IdavollApp` 构建后状态是完整的

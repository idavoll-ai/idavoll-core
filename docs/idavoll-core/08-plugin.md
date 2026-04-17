# Hooks 与 Plugins

## 概述

Core 当前的扩展机制由两部分构成：

- `IdavollPlugin`
- `HookBus`

它们的作用不是“替代继承”，而是让产品层以事件驱动方式挂接：

- 持久化
- topic / review / leveling
- session 级服务安装
- 经验固化

---

## `IdavollPlugin`

`idavoll/hook/base.py` 中的 `IdavollPlugin` 很薄：

```python
class IdavollPlugin:
    name = "plugin"
    def install(self, app) -> None:
        ...
```

插件约定在 `install(app)` 中完成：

- 注册 hook handler
- 保存 app 引用
- 接入产品层服务

安装入口：

```python
app.use(plugin)
```

---

## `HookBus`

`idavoll/hook/hooks.py` 提供一个极简异步事件总线。

方法：

- `on(event, handler)`
- `hook(event)` 装饰器
- `emit(event, **ctx)`

特点：

- handler 可以是 sync 或 async
- `emit()` 会用 `asyncio.gather()` 并发执行同一事件下的所有 handler
- 如果 handler 抛错，异常会向上传播给调用方

也就是说，HookBus 当前不是“吞错总线”，而是“并发执行的透明扩展点”。

---

## Core 内置事件

当前 Core 主动发出的事件包括：

- `agent.created`
- `agent.loaded`
- `on_session_start`
- `on_session_end`
- `pre_llm_call`
- `post_llm_call`
- `pre_tool_call`
- `post_tool_call`
- `on_pre_compress`
- `soul.refined`
- `subagent.completed`
- `subagent.failed`

其中 `on_pre_compress` 目前由 Core 自己注册了一个内置 handler：

- `flush_memories()`

---

## 产品层常见事件

Vingolf 在 Core 之上继续扩展了很多业务事件，例如：

- `topic.created`
- `topic.closed`
- `agent.level_up`
- `review.completed`

这些事件不是 Core 的一部分，但产品层依然复用同一套 `HookBus`。

---

## 当前推荐接法

产品层通常这样接入：

```python
@app.hooks.hook("on_session_start")
async def _on_session_start(session, **_):
    ...

@app.hooks.hook("on_session_end")
async def _on_session_end(session, **_):
    ...
```

常见职责：

- 在 `on_session_start` 给 `Session.services` 安装 factory
- 在 `agent.created` 持久化 `AgentProfile`
- 在 `on_session_end` 持久化 transcript
- 在 review / topic 事件里调用 Core 的 `generate_response()`

---

## Core 与产品层的边界

HookBus 的存在意味着：

- Core 不直接依赖 SQLite / Topic / Review
- 产品层也不需要 monkey patch `IdavollApp`

双方只通过：

- 显式 API
- 明确事件名

协作。

---

## 设计原则

- 插件只做扩展，不改写 Core 主流程
- 事件名要稳定、语义明确
- session / persistence / review 这类产品能力优先通过 hook 接入

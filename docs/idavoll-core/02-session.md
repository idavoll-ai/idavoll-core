# Session

## 概述

`idavoll/session/` 现在承载三类内容：

- 当前会话数据结构
- 上下文预算与压缩
- 会话级服务容器

它不再把跨历史 session 检索能力直接挂在 `Agent` 上，而是通过 `Session.services` 做按会话解析。

---

## 核心数据结构

### `Message`

`Message` 存在于 `session.py`，字段包括：

- `agent_id`
- `agent_name`
- `content`
- `role`
- `id`
- `created_at`
- `metadata`

它表示 session 历史里的单条消息，不区分 LangChain message 类型。

### `Session`

当前 `Session` 字段：

- `id`
- `participants`
- `metadata`
- `max_context_messages`
- `messages`
- `state`
- `services`
- `frozen_prompts`

其中最重要的两个新增边界是：

- `services`：会话级服务容器
- `frozen_prompts`：按 `agent_id` 缓存 frozen system prompt

### `SessionState`

枚举值：

- `open`
- `active`
- `closed`

Core 目前主要使用 `open / closed`，`active` 为产品层扩展预留。

---

## `SessionServices`

`SessionServices` 定义在 `context.py`。

当前内置能力只有一项：

- `session_search_factory`

通过：

```python
session.services.session_search_for(agent.id)
```

来解析某个 agent 在这个 session 中可用的跨历史 session 检索能力。

这层设计的意义：

- 不把会话能力挂在 `Agent` 顶层
- 不把产品层基础设施直接塞进 `Session`
- 多 agent session 下仍然可以按 `agent.id` 精确解析

---

## Frozen Prompt

`Session.frozen_prompts` 是当前 prompt 系统的关键约束。

规则：

- 只在某个 agent 首次参与该 session 时编译一次
- 缓存在 `frozen_prompts[agent.id]`
- 会话中途不重新编译
- 上下文压缩也不会修改它

这样做有两个目的：

- 保持 persona / frozen memory snapshot 稳定
- 避免对话越长，system prompt 也跟着漂移

---

## 上下文估算

`context.py` 目前只有一个粗略估算器：

```python
estimate_tokens(text) -> int
```

实现很简单：

- 空字符串返回 0
- 否则按 `len(text) // 4` 粗估 token

这个估算同时被：

- `ContextCompressor`
- `BuiltinMemoryProvider`
- `SQLiteSessionSearch`

用作 budget 控制。

---

## `ContextCompressor`

`compressor.py` 提供 `ContextCompressor`。

主入口：

- `maybe_compress(agent, session)`
- `compress(agent, session)`

压缩算法：

1. 保留头部 `head_keep`
2. 保留尾部 `tail_keep`
3. 中间消息作为 `middle`
4. 触发 `on_pre_compress`
5. 调用 LLM 把 `middle` 总结成一条 summary message
6. 用 `head + [summary] + tail` 替换原历史

不会被修改的内容：

- `session.frozen_prompts`

这意味着压缩只影响动态历史，不影响静态 system prompt。

---

## 压缩前记忆补写

Core 在 `IdavollApp` 启动时注册了一个内置 hook：

- `on_pre_compress -> flush_memories()`

`flush_memories()` 会：

1. 读取 frozen prompt 与近期对话
2. 加一个提醒 prompt
3. 只暴露 `memory` 工具
4. 给模型一次“压缩前补写长期记忆”的机会

这一步是为了避免上下文被压缩后，尚未写入 MEMORY.md 的长期事实丢失。

---

## 跨历史 Session 检索

`idavoll/session/search.py` 只保留了接口级 no-op：

```python
class SessionSearch:
    async def search(self, query: str, context: str = "") -> str:
        return ""
```

真正的实现现在在产品层。

### Vingolf 的接法

Vingolf 在 `on_session_start` 中给 `Session.services` 装 `session_search_factory`。

factory 会按 `agent_id` 懒创建：

- `SQLiteSessionSearch(repo, agent_id, llm=...)`

然后 Core 在 `generate_response()` / `generate_response_stream()` 里调用：

```python
search = session.services.session_search_for(agent.id)
session_ctx = await search.search(current_message, scene_context)
```

拿到的结果会并进本轮 `memory_context`。

---

## Session 与产品层的边界

Session 负责：

- 当前会话消息
- 当前会话状态
- 会话级服务解析
- frozen prompt 缓存

产品层负责：

- 原始 transcript 持久化
- 跨 session 检索实现
- topic / review / leveling 等业务上下文

---

## 设计原则

- `Session` 表示“当前会话”，不直接承载产品层基础设施对象
- 会话级能力通过 `services` 进入运行时
- 压缩只动动态历史，不动 frozen prompt
- 跨历史 session 检索是 session-scoped service，不是 `Agent` 字段

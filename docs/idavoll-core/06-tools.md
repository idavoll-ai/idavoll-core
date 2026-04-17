# Tools

## 概述

工具系统由三部分组成：

- `ToolSpec`
- `ToolRegistry`
- `ToolsetManager`

再加一层运行时绑定：

- `_runtime`
- `_agent`
- `_session`

这意味着“工具声明”和“工具执行上下文”已经被解耦。

---

## `ToolSpec`

`ToolSpec` 定义在 `idavoll/tools/registry.py`。

字段：

- `name`
- `description`
- `parameters`
- `fn`
- `tags`

注意：

- `fn` 可以为 `None`
- 这时工具只参与 prompt 展示，不参与 runtime dispatch

---

## `Toolset`

`Toolset` 用来声明一组工具或组合其它工具集。

字段：

- `name`
- `tools`
- `includes`
- `description`

解析规则是 depth-first：

- 先展开 `includes`
- 再追加当前 `tools`
- 保持首次出现顺序
- 自动去重

---

## `ToolRegistry`

`ToolRegistry` 是全局 append-only 注册表。

提供：

- `register(spec)`
- `get(name)`
- `get_or_raise(name)`
- `all()`
- `names()`
- `scan_module(module)`
- `scan_package(package)`

重复注册同名工具时，后者覆盖前者。

---

## `ToolsetManager`

`ToolsetManager` 负责：

- `define(toolset)`
- `resolve(enabled_toolsets, disabled_tools=...)`
- `build_index(enabled_toolsets, disabled_tools=...)`

`resolve()` 返回的是当前 agent 真正可用的 `ToolSpec` 列表。  
`build_index()` 返回的是给 prompt 用的 markdown 工具索引。

---

## `@tool` 装饰器

`@tool(...)` 会把 `ToolSpec` 挂到函数的 `__tool_spec__` 上。

它不会包裹函数逻辑本身，只做声明注册。

因此工具函数仍然是普通 Python callable，便于：

- 单元测试
- partial 注入
- provider 替换

---

## 当前内置工具

Core 当前注册的 builtin tools：

- `memory`
- `reflect`
- `session_search`
- `skill_get`
- `skill_patch`
- `task_tool`

对应工具集：

- `memory`
- `skills`
- `builtin`
- `task`

其中：

- `builtin = memory + skills`
- `task` 单独暴露 subagent delegation 能力

---

## 三层注入模型

这是当前工具系统和旧架构最大的区别之一。

### 1. 注册时注入 `_runtime`

`task_tool` 在 `IdavollApp._register_builtin_tools()` 中用：

```python
functools.partial(task_tool_fn, _runtime=self.subagent_runtime)
```

预绑定 runtime。

### 2. Agent 级注入 `_agent`

`IdavollApp._bind_agent_tools()` 会扫描工具签名，给所有声明了 `_agent` 的工具做 partial。

这一步通常发生在：

- agent 创建
- agent 加载
- 工具集解锁后

### 3. Turn 级注入 `_session`

`IdavollApp._bind_turn_tools()` 会在每次 `generate_response()` / `generate_response_stream()` 里做本轮绑定。

当前最典型的例子就是：

- `session_search(query, *, _agent, _session)`

它需要当前 `Session.services` 才能解析到真正的检索能力，因此不能永久绑到 `Agent` 上。

---

## 工具执行循环

`IdavollApp.generate_response()` 里的 tool loop 逻辑是：

1. 取出 `callable_tools`
2. `llm.invoke(..., tools=callable_tools)`
3. 检查 `AIMessage.tool_calls`
4. 按 `ToolSpec.name -> spec.fn` 分发
5. 执行结果封装成 `ToolMessage`
6. 回到下一轮，直到没有 tool call 或达到安全上限

hook 触发点：

- `pre_tool_call`
- `post_tool_call`

---

## 与 Prompt 的关系

Prompt 里展示的 tool list 来自 `ToolsetManager.build_index()`，不是运行时 partial 后的 callable。

因此：

- Prompt 关注“当前 agent 声明有哪些工具”
- Runtime 关注“本轮执行时这些工具拿到了哪些注入上下文”

---

## 设计原则

- 工具声明与执行上下文分离
- Agent 级能力和 Session 级能力分别注入
- Toolset 只表达权限与可见性，不表达 runtime 状态
- Tool loop 仍然是普通 LLM 回合的一部分，不单独引入新的调度器

# Subagent

## 概述

Core 现在已经没有 scheduler 模块了。  
原来的 `11-scheduling` 章节应由 `SubagentRuntime` 取代。

当前子任务能力由两部分组成：

- `SubagentRuntime`
- `task_tool`

它们提供的是“临时子 Agent 委派”，不是后台调度。

---

## 数据模型

`idavoll/subagent/models.py` 定义了两层模型：

### 内部模型

- `SubagentSpec`
- `SubagentResult`

### 对外模型

- `TaskToolRequest`
- `TaskToolResult`

其中 `TaskToolRequest / Result` 是产品层和 LLM 工具层真正应该依赖的接口。

---

## `task_tool`

`task_tool` 是当前唯一公开的子任务入口。

它在注册时预绑定：

- `_runtime = SubagentRuntime`

在 agent 绑定时再注入：

- `_agent`

模型看到的参数主要是：

- `goal`
- `context`
- `role`
- `blocked_tools`
- `memory_mode`
- `max_turns`
- `timeout_seconds`

返回值是 JSON 序列化后的 `TaskToolResult`。

---

## `SubagentRuntime`

`SubagentRuntime` 实例化于 `IdavollApp`：

```python
self.subagent_runtime = SubagentRuntime(self)
```

主入口：

- `task_tool(parent_agent, request, scene_context="")`

内部还有：

- `_run_subagent()`
- `_run_subagents_in_parallel()`
- `_spawn_subagent()`
- `_resolve_child_tools()`
- `_build_child_prompt()`

---

## 运行约束

当前 runtime 约束如下：

- 子 agent 复用现有 `Agent` 类，不新增 `Subagent` 类
- 子 agent 不继承父 session 历史
- 默认最大深度 `DEFAULT_MAX_DEPTH = 1`
- 默认并发数 `DEFAULT_MAX_CONCURRENT = 4`
- 每个子任务用 `asyncio.wait_for()` 做超时控制

默认永久封锁的工具：

- `memory`
- `skill_patch`
- `clarify`
- `send_message`
- `task_tool`

其中 `task_tool` 被封锁是为了防止 reviewer 再次 spawn reviewer。

---

## 子 Agent 的构造方式

`_spawn_subagent()` 当前会：

1. 创建新的 `AgentProfile`
2. 用现有 `Agent` dataclass 构造 child
3. 在 `metadata` 里记录运行时标记
4. 重新解析 child.tools
5. 重新执行 `_bind_agent_tools()`

典型 metadata：

- `runtime_mode="subagent"`
- `parent_agent_id`
- `delegate_depth`
- `memory_mode`
- `review_role`

这意味着 subagent 是“运行模式”，不是“新的对象类型”。

---

## Memory 与 Tool 权限

当前 memory 策略：

- `memory_mode="disabled"`：child.memory 保持 `None`
- `memory_mode="readonly"`：可以挂 memory，但写工具仍可通过 blocked list 控制

工具解析策略：

1. 默认继承父 agent 的工具名
2. 重新从全局 registry 取 fresh specs
3. 去掉 blocked tools
4. 可额外合并显式要求的 toolsets

因此 child 不会复用父 agent 已经 partial 过的工具对象。

---

## 与 `generate_response()` 的关系

子任务本质上仍然调用 Core 主回复路径：

```python
await self._app.generate_response(
    child,
    session=None,
    scene_context=scene_context,
    current_message=user_turn,
    system_message=system_message,
)
```

关键点：

- `session=None`
- fresh context
- child system prompt 由 `_build_child_prompt()` 单独生成

所以 subagent 不是额外的执行引擎，而是对现有回复引擎的一层受限包装。

---

## Hooks

当前 subagent runtime 会发出：

- `subagent.completed`
- `subagent.failed`

产品层可以在这些节点做：

- review 汇总
- telemetry
- 失败审计

---

## 设计原则

- 子任务是受限运行模式，不是新的 Agent 类型
- 不继承父 session 历史
- 默认禁止副作用和递归委派
- 并发、超时、深度限制都在 runtime 层收口

# Prompt

## 概述

当前 prompt 系统有两个组件：

- `PromptCompiler`
- `PromptBuilder`

其中真正的主路径是 `PromptCompiler`。  
`PromptBuilder` 现在更像兼容层或最小装配器，保留给测试和轻量路径使用。

---

## `PromptCompiler`

`idavoll/prompt/compiler.py` 是当前 Core 的主实现。

它把 prompt 拆成两部分：

- frozen system prompt
- dynamic turn messages

### Frozen system prompt

通过 `compile_system(agent, system_message="")` 构建。

构成顺序：

1. Identity / Voice
2. 可选 `system_message`
3. Frozen memory snapshot
4. Skills Index
5. Tool guidance
6. Post instructions

来源分别是：

- SOUL.md 或 profile fallback
- 调用方传入
- `agent.memory.system_prompt_block()`
- `agent.skills.build_index()`
- `ToolsetManager.build_index()`

输出会被缓存进：

```python
session.frozen_prompts[agent.id]
```

不会在 session 中途重新编译。

### Dynamic turn

通过 `build_turn(frozen_system, session, ...)` 构建。

顺序：

1. `SystemMessage(frozen_system)`
2. 动态上下文块
3. 会话历史
4. 当前用户消息

动态上下文块当前包含：

- `memory_context`
- `scene_context`

如果没有当前消息且只有 system prompt，会补一句：

```text
请根据当前场景发言。
```

---

## Identity / Voice 来源

`PromptCompiler._identity_and_voice()` 的优先级：

1. 优先读取 workspace 中的 SOUL.md
2. 扫描安全风险
3. 能解析成 `SoulSpec` 就编译成规范人格块
4. 解析失败则直接保留原始 SOUL.md 文本
5. 如果连 SOUL.md 都没有，再回退到 `AgentProfile` 的通用描述

这让系统同时兼容：

- 结构化生成的 SOUL.md
- 手写遗留 SOUL.md
- 无 workspace 的测试态 agent

---

## Skills 与 Tools 在 Prompt 中的作用

`PromptCompiler` 不直接解析 tool 实现，也不关心调用时注入。

它只关心两件事：

- `skills_index`
- `tool_guidance`

其中 `tool_guidance` 默认来自 `ToolsetManager.build_index()`，因此 prompt 中展示的是“agent 当前启用的工具声明”，不是运行时临时注入后的 partial 函数。

---

## `PromptBuilder`

`idavoll/prompt/builder.py` 仍然存在，但它是更早的简化版本。

特点：

- 直接把 identity、memory、scene 拼成一个 system message
- 没有 frozen prompt 缓存分层
- 没有 `ToolsetManager` 参与

适合：

- 低成本测试
- 不需要 frozen/dynamic 分层的轻量调用

不适合替代当前主路径。

---

## 与 Safety 的关系

`PromptCompiler` 是 `SafetyScanner` 最主要的调用方。

会被扫描的内容：

- SOUL.md
- Skills Index

如果发现危险内容，会抛出 `SafetyScanError`，中止 prompt 编译，而不是把恶意文本静默送进模型。

---

## 设计原则

- Frozen 与 Dynamic 明确分层
- SOUL.md 是 persona 真相来源
- prompt 中展示工具声明，不耦合具体 runtime 注入
- 保留 `PromptBuilder` 作为轻量兼容层，但主路径统一走 `PromptCompiler`

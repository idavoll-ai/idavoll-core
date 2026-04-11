# Prompt 模块

## 概述

`idavoll/prompt/` 负责将 Agent 的人格、记忆、技能、工具等信息组装为 LLM 可消费的消息列表。包含两个类：旧的简单组装器 `PromptBuilder` 和当前主用的 `PromptCompiler`。

---

## PromptCompiler（`compiler.py`）

当前框架主用的 Prompt 编译器，实现**静态 System Prompt + 动态 Turn 消息**的两阶段模式。

### 静态 System Prompt（`compile_system()`）

在 Session 内每个 Agent **第一次发言前**编译一次，存入 `session.frozen_prompts[agent.id]`，之后不再重编译（Frozen Snapshot 原则）。

System Prompt 由以下 Slot 顺序拼接：

| Slot | 内容来源 | 说明 |
|------|---------|------|
| [0]+[1] | SOUL.md / AgentProfile | 身份与语音风格 |
| [2] | 调用方传入 `system_message` | 可选的外部指令 |
| [3-5] | `MemoryManager.system_prompt_block()` | 冻结记忆快照（MEMORY.md + USER.md） |
| [6] | `SkillsLibrary.build_index()` | 技能索引，列出所有 active 技能 |
| [7] | `workspace/PROJECT.md` | 项目上下文（可选） |
| [8] | `ToolsetManager.build_index()` | 工具索引，列出已激活工具 |
| [9] | 固定指令 | "保持人设，自然表达，直接回应当前场景。" |

**SOUL.md 优先**：若 workspace 存在且 SOUL.md 非空，优先使用解析后的 `SoulSpec` 编译人格块；否则回退到 AgentProfile.description 的通用描述。

### 动态 Turn（`build_turn()`）

每轮对话调用前临时组装，**不存储**：

```
[SystemMessage(frozen_system)]
[SystemMessage(memory_context + scene_context)]   ← 动态上下文块
[AIMessage(past_reply_1)]
[HumanMessage(past_user_1)]
...
[HumanMessage(current_message)]
```

### 安全扫描

用户可编辑内容（SOUL.md、PROJECT.md、技能索引）在注入前通过 `SafetyScanner.scan()` 检查，发现违规则抛出 `SafetyScanError` 中止编译。传入 `scanner=None` 仅用于测试。

---

## PromptBuilder（`builder.py`）

早期的简化组装器，不实现 Slot 分离和冻结快照，直接在每次调用时重新组装完整消息列表。仍保留用于轻量场景或测试，不是生产路径的主用类。

组装顺序：

1. 从 SOUL.md 解析人格（失败时回退到通用描述）
2. 注入 `memory_context` 和 `scene_context`
3. 追加对话历史（`session.recent_messages()`）
4. 追加当前消息

---

## 设计原则

- **静态与动态分离**：System Prompt 冻结确保 Agent 在 Session 内行为一致，即使底层文件在 Session 中间被修改也不影响当前对话
- **Slot 顺序固定**：各内容块的顺序在规范中明确定义，避免不同调用路径产生顺序差异
- **SOUL.md 是唯一人格来源**：Prompt 中的身份描述始终从 SOUL.md 渲染，不依赖 AgentProfile.description（后者仅用于管理展示）

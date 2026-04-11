# Session 模块

## 概述

`idavoll/session/` 管理单次交互的生命周期：消息存储、上下文估算、历史压缩、跨 Session 经验检索。

---

## 核心数据结构（`session.py`）

### Message

会话中的单条消息：

```python
@dataclass
class Message:
    agent_id: str
    agent_name: str
    content: str
    role: Literal["user", "assistant"]
    id: str           # UUID
    created_at: datetime
    metadata: dict
```

### Session

会话对象，是核心运行时与产品模块共享的交互空间：

```python
class Session:
    id: str                          # UUID
    participants: list[Agent]        # 参与者列表
    messages: list[Message]          # 完整消息历史
    state: SessionState              # OPEN / ACTIVE / CLOSED
    frozen_prompts: dict[str, str]   # agent_id → 冻结的 System Prompt
    max_context_messages: int        # 滑动窗口大小（默认 20）
```

### 冻结 Prompt 原则（§9.2 Frozen Snapshot）

`frozen_prompts` 在 Session 内每个 Agent 的**第一轮**被懒加载编译，之后整个 Session 生命周期内不再重编译。这确保了 Agent 在同一场对话中行为的一致性，即使 SOUL.md 或 MEMORY.md 在会话期间被修改也不会影响当前对话。

---

## 上下文估算（`context.py`）

`estimate_tokens(text) -> int` 提供快速 token 估算（`len(text) // 4`），用于压缩触发判断和记忆 prefetch 预算控制，不需要精确值。

---

## 上下文压缩（`compressor.py`）

`ContextCompressor` 在会话历史超出配置阈值时自动压缩，避免超出 LLM 上下文窗口。

### 算法

```
当 session.messages 估算 token 数超过 token_threshold 时：

1. 切分：
   - head  = 前 head_keep 条消息（上下文锚点，始终保留）
   - tail  = 后 tail_keep 条消息（近期上下文，始终保留）
   - middle = 其余消息（压缩候选）

2. 触发 on_pre_compress 钩子，让插件在消息消失前提取持久事实

3. 调用 LLM 将 middle 压缩为一段 ≤200 字的结构化摘要

4. 用 [summary_msg] 替换 middle，最终：
   session.messages = head + [summary_msg] + tail
```

### 配置（CompressionConfig）

| 参数 | 说明 |
|------|------|
| `enabled` | 是否启用压缩 |
| `token_threshold` | 触发压缩的 token 上限 |
| `min_messages` | 触发压缩所需的最少消息数 |
| `head_keep` | 保留的头部消息数 |
| `tail_keep` | 保留的尾部消息数 |

冻结的 `frozen_prompts` 不参与压缩，只有 `session.messages` 被修改。

---

## 跨 Session 检索（`search.py`）

`SessionSearch` 从历史 Session 摘要文件中检索与当前对话相关的过往经验，填补"我记得发生过这件事，但它还不够重要到写入 MEMORY.md"的空白。

### 数据来源

`ExperienceConsolidator` 在每次 Session 关闭后写入 `workspace/sessions/{session_id}.md`，格式：

```markdown
# Session Summary

- **Session ID**: <uuid>
- **Date**: <YYYY-MM-DD HH:MM UTC>
- **Participants**: <逗号分隔名称>
- **Facts written**: <n>

## Key Points

- ...
```

### 搜索策略（MVP，无向量嵌入）

1. 对 query + context 分词（支持中英文混合）
2. 对每条 Session 记录按关键词重叠数打分
3. 返回 top-N 记录，格式化为 `<session-context>` 块，受 `token_budget` 约束
4. 无匹配时返回空字符串

### 返回格式

```xml
<session-context>
[2025-03-01 09:30 UTC] Session a1b2c3d4
Participants: Alice, Bob
- 关键要点 1
- 关键要点 2
</session-context>
```

该块由 `IdavollApp.generate_response` 自动附加到 `memory_context`，不需要调用方显式处理。

---

## 设计原则

- **Session 是临时的**：Session 结束后，持久化价值通过 `ExperienceConsolidator` 提取到 MEMORY.md 和 sessions/ 目录
- **压缩保留边界**：head/tail 策略确保对话的起点和最近上下文始终可见
- **跨 Session 召回与持久记忆正交**：SessionSearch 不替代 MEMORY.md，二者由不同路径写入，服务不同的召回场景

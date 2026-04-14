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

## 跨 Session 检索（`search.py` + `vingolf/persistence/session_repo.py`）

`agent.session_search` 接口统一为一个鸭子类型：只需实现 `async def search(query, context) -> str`。

### Core 层：no-op 存根

`idavoll/session/search.py` 的 `SessionSearch` 是一个空实现，当产品层未配置存储时使用，`search()` 始终返回空字符串。

### Vingolf 层：`SQLiteSessionSearch`

`vingolf/persistence/session_repo.py` 提供完整实现，在 `VingolfApp.startup()` 时通过 `_attach_session_search()` 替换掉每个 Agent 上的 no-op 存根。

**数据来源：** `VingolfApp` 监听 `on_session_end` 和 `topic.closed` 事件，将原始对话写入 `session_records` 表（`SessionRecordRepository.save()`）。

**搜索策略（无向量嵌入）：**

1. 过滤出该 Agent 参与过的 session 记录
2. 对 query + context 分词，按关键词命中数打分
3. 对 top-N 命中记录调用 LLM 生成摘要（LLM 失败时降级为文本摘录）
4. 合并结果为 `<session-context>` 块，受 `token_budget` 约束

### 返回格式

```xml
<session-context>
[Session a1b2c3d4]
- 关键要点 1
- 关键要点 2

---

[Session b5c6d7e8]
- 关键要点 3
</session-context>
```

该块由 `IdavollApp.generate_response` 自动附加到 `memory_context`，调用方无需手动处理。

---

## 设计原则

- **Session 是临时的**：Session 结束后，持久化价值一部分提取到 MEMORY.md，另一部分以原始对话形式写入 SQLite `session_records`
- **压缩保留边界**：head/tail 策略确保对话的起点和最近上下文始终可见
- **跨 Session 召回与持久记忆正交**：SessionSearch 不替代 MEMORY.md，二者由不同路径写入，服务不同的召回场景

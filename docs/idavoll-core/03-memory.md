# Memory 模块

## 概述

`idavoll/memory/` 实现 Agent 的记忆系统，分为三层：抽象接口、内置实现、记忆管理器。Session 关闭时由认知引擎（`cognition/engine.py`）驱动事实提取和摘要写入。

---

## 抽象接口（`base.py`）

`MemoryProvider` 是所有记忆后端的抽象基类，定义三个核心生命周期方法：

| 方法 | 调用时机 | 说明 |
|------|----------|------|
| `system_prompt_block()` | Session 启动时，一次 | 返回注入 System Prompt 的静态记忆块（冻结） |
| `prefetch(query, context)` | 每轮对话开始 | 返回与当前消息相关的记忆，注入为 `<memory-context>` |
| `sync_turn(user_msg, assistant_msg)` | 每轮对话结束 | 通知 Provider 一轮已完成（内置 Provider 为 no-op） |
| `write_fact(content, target)` | 由认知引擎调用 | 持久化一条事实，返回 True/False |

---

## 内置实现（`builtin.py`）

`BuiltinMemoryProvider` 读写 ProfileWorkspace 中的 `MEMORY.md` 和 `USER.md`。

### system_prompt_block()

在 Session 启动时读取两个文件，合并为：

```markdown
## Agent Memory

- 事实1
- 事实2

## User Profile

- 用户偏好1
```

超出 `system_block_token_budget`（默认 400 token）时截断。

### prefetch(query)

无向量嵌入的关键词召回策略：

1. 从 MEMORY.md 和 USER.md 解析所有 bullet 事实
2. 对 query 分词，计算每条事实的关键词命中数
3. 按命中数降序排列，取 top-N 直到达到 `prefetch_token_budget`（默认 200 token）
4. 返回 `<memory-context>` 块，无匹配时返回空字符串

### write_fact(content, target)

写入持久化事实的硬约束：

- 内容非空
- 长度 ≤ 500 字符
- 不含注入模式（由正则检测）
- 不是已有事实的精确重复（去重）

`target` 可以是 `"memory"` 或 `"user"`，分别写入对应文件。

---

## 记忆管理器（`manager.py`）

`MemoryManager` 是 Session 运行时对记忆的唯一入口，支持挂载多个 `MemoryProvider`。

```python
manager = MemoryManager()
manager.add_provider(BuiltinMemoryProvider(workspace))
# 可继续 add_provider() 挂载向量数据库等外部 Provider
```

各方法均按注册顺序遍历所有 Provider：

- `system_prompt_block()` → 合并所有非空块（换行分隔）
- `prefetch()` → 合并所有 Provider 的返回（换行分隔）
- `sync_turn()` → 依次通知所有 Provider
- `write_fact()` → 写入第一个接受写入的 Provider，返回 True

---

## 认知引擎（`cognition/engine.py`）

`ExperienceConsolidator` 在 Session 关闭时由 `IdavollApp.close_session()` 调用，驱动完整的经验固化流程（§4.2 / §8.4）。

### 流程

```
Session 关闭
    │
    ├─ 1. 格式化最近 N 条消息为对话文本
    │
    ├─ 2. LLM 提取事实 → MemoryManager.write_fact()
    │      ├─ target="memory"  → MEMORY.md
    │      └─ target="user"    → USER.md
    │
    ├─ 3. LLM 生成 Session 摘要 → workspace/sessions/{id}.md
    │
    ├─ 4. LLM 判断是否存在可复用技能 → SkillsLibrary.create/patch()
    │
    └─ 5. 触发 consolidation.completed 钩子
```

### 事实提取原则

- **保留**：偏好、纠正、环境特殊性、反复有效的结论
- **不保留**：任务日志、临时 TODO、逐步推理过程、单次性细节
- 每条事实不超过 100 字，表达完整、独立

### ConsolidationResult

记录本次固化的结果：

```python
@dataclass
class ConsolidationResult:
    session_id: str
    facts_written: int    # 成功写入的事实数
    facts_skipped: int    # 被跳过（重复）的事实数
    skills_saved: int     # 保存的技能数
    summary_path: str     # Session 摘要文件路径
    errors: list[str]     # 写入时遇到的错误
```

---

## 设计原则

- **两类记忆正交**：`system_prompt_block()` 是冻结的静态知识；`prefetch()` 是每轮动态召回，二者服务不同目的
- **写入由认知引擎驱动**：`BuiltinMemoryProvider.sync_turn()` 是 no-op，避免每轮对话都写磁盘
- **持久化与召回分离**：记忆写入路径（认知引擎 → write_fact）与读取路径（prefetch）完全独立

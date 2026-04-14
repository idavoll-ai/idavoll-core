# Memory 模块

## 概述

`idavoll/memory/` 实现 Agent 的记忆系统，分为三层：抽象接口、内置实现、记忆管理器。事实写入由 Agent 在对话中通过内置 `memory` 工具主动触发，Session 关闭后由产品层负责持久化原始对话。

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

## 设计原则

- **两类记忆正交**：`system_prompt_block()` 是冻结的静态知识；`prefetch()` 是每轮动态召回，二者服务不同目的
- **写入由 Agent 主动触发**：`BuiltinMemoryProvider.sync_turn()` 是 no-op，事实写入通过 Agent 在对话中调用内置 `memory` 工具或产品层在 `on_session_end` 钩子中驱动
- **持久化与召回分离**：记忆写入路径（write_fact）与读取路径（prefetch）完全独立

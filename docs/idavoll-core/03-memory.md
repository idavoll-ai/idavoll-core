# Memory

## 概述

当前 memory 系统被拆成 4 层：

1. `MemoryStore`
2. `MemoryProvider`
3. `BuiltinMemoryProvider`
4. `MemoryManager`

再加两个运行时协作者：

- `memory` / `reflect` tools
- `flush_memories()`

这套结构的核心变化是：

- 事实 CRUD 不再走 `MemoryManager`
- durable write 直接落到 `MemoryStore`
- `MemoryManager` 只负责 provider 编排和写后广播

---

## `MemoryStore`

`idavoll/memory/store.py` 是 durable facts 的唯一文件 I/O 层。

管理两个目标文件：

- `MEMORY.md`
- `USER.md`

主要能力：

- `load_snapshot()`
- `format_for_system_prompt(target)`
- `read_raw()` / `write_raw()`
- `read_facts()`
- `add_fact()`
- `replace_fact()`
- `remove_fact()`

### Snapshot 合约

`load_snapshot()` 必须在 session 开始时调用一次。  
它会把当前 MEMORY / USER 内容冻结进 `_snapshot`。

之后：

- `format_for_system_prompt()` 永远返回 frozen snapshot
- 中途工具写入会更新磁盘文件
- 但不会更新 frozen snapshot

这样可以保证整个 session 内 system prompt 稳定。

### 约束

`MemoryStore` 对写入内容做硬约束：

- 不能为空
- 最大长度 500 字符
- 禁止 prompt injection 关键词
- `replace/remove` 使用 substring match，并在歧义时抛错

---

## `MemoryProvider`

`idavoll/memory/base.py` 定义 provider 接口：

- `system_prompt_block()`
- `prefetch(query, context)`
- `sync_turn(user_msg, assistant_msg)`

可选 hook：

- `on_memory_write(action, target, content)`
- `get_tool_specs()`

因此 provider 的职责是“为 prompt 和 turn 提供记忆能力”，不是自己做持久文件 CRUD。

---

## `BuiltinMemoryProvider`

`BuiltinMemoryProvider` 是 `MemoryStore -> MemoryProvider` 的适配层。

### `system_prompt_block()`

从 `MemoryStore` 的 frozen snapshot 读取 MEMORY / USER，渲染为 system prompt 块。

输出里会包含：

- 记忆使用规则
- `MEMORY（你的笔记）`
- `USER PROFILE（用户档案）`

并受 `system_block_token_budget` 限制。

### `prefetch(query, context)`

当前是 MVP 级关键词召回：

1. 读取 live facts
2. 用 query + context 做分词
3. 统计 token 命中数
4. 返回 top facts
5. 渲染成 `<memory-context> ... </memory-context>`

它不是 embedding 检索，但接口已经允许后续 provider 替换实现。

### `sync_turn()`

对 builtin provider 来说是 no-op。  
长期写入不靠 turn 自动总结，而是靠工具层显式写入。

---

## `MemoryManager`

`MemoryManager` 管理一个或多个 `MemoryProvider`。

公开接口：

- `add_provider(provider)`
- `system_prompt_block()`
- `prefetch(query, context)`
- `sync_turn(user_msg, assistant_msg)`
- `get_tool_specs()`
- `on_memory_write(action, target, content)`

注意：

- 它不提供 `add_fact()` / `replace_fact()` / `remove_fact()`
- provider 按注册顺序执行
- `get_tool_specs()` 会去重合并外部 provider 暴露的工具

---

## 工具层

内置 memory 相关工具现在都在 `idavoll/tools/builtin/`：

- `memory`
- `reflect`

### `memory`

`memory` tool 直接调用 `agent.memory_store`：

- `add`
- `replace`
- `remove`
- `read`

写成功后会调用：

```python
agent.memory.on_memory_write(...)
```

通知所有 provider 镜像写入。

### `reflect`

`reflect` 用于批量把高阶洞察写入 MEMORY.md。

它适合保存：

- 模式
- 经验
- 对话级总结

而不是单条具体事实。

---

## 压缩前补写

`idavoll/memory/flush.py` 的 `flush_memories()` 是 memory 系统和 session compressor 的桥接点。

触发时机：

- `on_pre_compress`

行为：

1. 构造一组仅包含近期对话的 messages
2. 只暴露 `memory` tool
3. 给模型一次补写遗漏长期记忆的机会

这意味着 memory 不是只在正常对话里工作，也会在“即将压缩上下文”的边界上被主动调用。

---

## 当前边界

Memory 系统内部边界现在很清楚：

- `MemoryStore`：磁盘真相
- `BuiltinMemoryProvider`：prompt-facing 读取适配
- `MemoryManager`：provider orchestration
- `memory / reflect`：显式写入入口
- `flush_memories()`：压缩前保护机制

---

## 设计原则

- frozen prompt 只读 snapshot，不读 live state
- durable write 直接走 `MemoryStore`
- provider 接口负责“取”和“同步”，不负责核心 CRUD
- 外部 provider 可以镜像写入，也可以贡献额外工具

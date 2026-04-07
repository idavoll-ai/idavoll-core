# Agent-Scoped Session 重构思路

## 背景

当前 Idavoll 的运行时，使用一个共享的 `Session` 对象来同时表达：

- 共享的讨论空间
- 当前活跃的参与者集合
- 短期对话历史
- 记忆提炼、评审、调度等流程的生命周期边界

这种模型在 MVP 阶段是成立的，因为那时一个 topic 基本就等于一场完整且封闭的讨论。

但在真实业务场景中，它会逐渐暴露出限制，主要有以下几个原因：

1. "session" 应该属于某个具体 agent，而不是属于整个房间。
2. 当话题已经处于 active 状态时，agent 仍然可能随时进入或退出。当前 `Session.add_participant()` 在 state 不为 `OPEN` 时直接抛出 `RuntimeError`，这不是产品决策，而是一个被遗留的架构约束，需要在本次重构中解除。
3. 业务中并不只有一种交互场景，框架不应该对交互形态做任何假设，具体的场景语义应该由产品层定义。

这份文档只保留架构层面的思考，重点讨论重构方向，而不进入具体数据结构设计。

---

## 层级边界

本次重构涉及两层，需要明确各自的职责边界：

- **`idavoll`（框架层）**：提供通用的运行时原语。不了解"辩论"、"评论"、"评价"等任何业务概念。只关心 agent 的参与状态、内容的结构关系、执行的调度机制。
- **`vingolf`（产品层）**：在框架原语之上，通过 Plugin 定义具体的交互场景、内容类型语义、调度策略和聚合规则。

凡是与具体业务形态相关的概念，都不应该进入 `idavoll`。

---

## 当前代码中的具体并发缺陷

这是最优先需要理解的问题，因为它直接决定了迁移的首要步骤。

`app.py` 中的 `run_session` 循环，在每一轮 `agent.before_generate` 钩子执行后，通过以下方式传递 per-agent 上下文：

```python
session.metadata["_memory_context"] = memory_text   # 由 memory hook 写入
session.metadata.pop("_memory_context", "")          # 由 run_session 读取
scene_context = session.metadata.get("scene_context", "")  # 由 TopicPlugin 写入
```

这个设计的根本问题是：`_memory_context` 和 `scene_context` 都写在同一个 forum 级别的 `session.metadata` 里，属于 per-agent 的信息却被当作共享状态传递。

当前之所以没有出错，是因为 `run_session` 的循环是严格顺序的：同一时刻只有一个 agent 在 generate。一旦引入并发生成（多个 agent 同时响应），`before_generate` hook 会互相覆盖对方写入的 `_memory_context`，导致错误的 prompt 被发送给错误的 agent。

这个 hazard 不需要等到完整的架构重构才能修，它是最先应该处理的迁移步骤。

---

## 核心问题

当前 core 抽象把两类本应分离的运行时状态混在了一起：

- 属于共享空间的状态
- 属于某个 agent 参与过程的私有状态

这正是当前架构上的核心错位。

只要这两类状态继续被放在同一个共享 `Session` 里，系统就会持续在下面这些问题上遇到阻力：

- active 状态下的加入和离开
- 每个 agent 的局部上下文
- 并发生成
- 产品层对多种交互模式的扩展

所以这个问题并不主要出在产品插件层，而是来自 core runtime 选取的边界本身。

---

## 设计目标

- 将共享事实与 agent 私有运行时状态分离。
- 让 agent-owned 的 `Seat` 成为一等概念。
- 在不破坏整体会话模型的前提下，支持 active join、leave 和 rejoin。
- 框架层保持对业务形态的无知，所有业务语义由产品层通过 Plugin 注入。
- 将最终结算、排名、评审结果与成长，保持为产品层的聚合关注点。

---

## idavoll 框架层的抽象

### 命名约定

| 概念 | 名称 | 说明 |
|------|------|------|
| 共享交互空间 | `Forum` | 替代当前 `Session` 的共享空间语义 |
| agent 参与过程 | `Seat` | agent-owned，本次重构引入的核心新概念 |
| 单次执行动作 | `Turn` | 一次 scheduler 调度出来的生成尝试 |
| 交互内容单元 | `Statement` | 替代当前的 `Message`，结构上支持引用关系 |

### 1. Forum（共享交互空间）

框架层的 `Forum` 只关心：

- 生命周期（open / active / closed）
- `Statement` 的有序集合
- 当前持有哪些 `Seat`
- `forum.metadata`：forum 级别的插件共享状态

`Forum` 不了解"话题"、"辩论"、"评论楼"等任何业务概念。这些语义由 vingolf 的 Plugin 通过 `forum.metadata` 和 hook 注入。

对应现有代码：`Session` 的共享空间部分迁移至此；`session.metadata` 的 forum 级条目迁移至 `forum.metadata`。

### 2. Seat（Agent-owned 参与过程）

`Seat` 表示某个 agent 在 `Forum` 中的本地参与状态。

框架层的 `Seat` 承载：

- 参与状态：`joining` / `active` / `paused` / `leaving` / `returning`
- `seat.local_context`：per-agent 隔离的短期上下文（`_memory_context`、插件注入的 per-agent scene context）
- join 时间戳（用于 late join 时确定 visibility 范围）
- 是否当前可被调度

`Seat` 不了解 agent 在讨论的是什么，也不了解其局部状态的业务含义。

Memory consolidation 的触发时机从 `session.closed` 迁移至 `seat.closed`，使 agent 离开时即可独立触发，不必等待整个 forum 关闭。

### 3. Turn（执行单元）

`Turn` 表示一次被调度出来的生成动作。

框架层的 `Turn` 承载：

- 当前执行的是哪个 `Seat`
- 本次执行基于的 forum 快照
- 执行结果：成功、失败、已提交

`Turn` 不了解本次动作的业务意图（是评论还是辩论发言）。意图由产品层通过 `Turn.metadata` 或 hook 传递给 prompt 构建逻辑。

将其变成一等对象的意义在于：
- 支持取消和重试
- 支持并发多个 agent 同时持有各自的 `Turn`
- 调度器可以推理"哪些 seat 当前可以出 turn"

### 4. Statement（通用交互内容层）

`Statement` 是对当前 `Message` 的泛化。

框架层的 `Statement` 只有：

```
Statement:
  id
  author_id     # 产出这条内容的 agent
  content       # 文本内容
  type: str     # 完全开放，框架不枚举任何值
  parent_id?    # 可选，指向另一条 Statement（支持树/图结构）
  metadata: dict
```

`type` 字段的具体取值和语义完全由产品层定义，框架不做任何枚举或约束。`parent_id` 使内容结构从线性列表变为可带引用关系的图，框架只保证引用关系的存储，不解释其含义。

### 5. HookBus 的作用域

当前 `agent.before_generate` 同时承载了两类写入：

```python
# TopicPlugin 写的：forum 级别
session.metadata["scene_context"] = topic_description

# memory hook 写的：agent 级别
session.metadata["_memory_context"] = memory_text
```

这两件事写在同一个 hook 里，之所以没有出问题，是因为 `session.metadata` 是共享的，没有强制区分写入目标。拆分后如果 hook 不同步调整，插件开发者仍然面对"该写 `forum.metadata` 还是 `seat.local_context`"的模糊，只是换了一种形式的混乱。

因此 `agent.before_generate` 需要拆成两个职责明确的 hook：

| Hook | Payload | 写入目标 | 用途 |
|------|---------|----------|------|
| `forum.before_turn` | `(forum, turn)` | `forum.metadata` | 注入 forum 级共享上下文，如 topic 描述、debate 规则 |
| `seat.before_generate` | `(seat, turn)` | `seat.local_context` | 注入 per-agent 上下文，如 memory context、agent 的局部 scene context |

两个 hook 的 payload 中不再暴露裸的 `session.metadata`，插件没有写错位置的机会。

框架层完整的 hook 列表：

**Forum 级**：`forum.created`、`forum.closed`、`forum.before_turn`、`forum.statement.after`

**Seat 级**：`seat.joined`、`seat.left`、`seat.before_generate`

**Turn 级**：`turn.completed`、`turn.failed`

迁移影响：`TopicPlugin` 的 `before_generate` handler 需要拆分——forum 级写入（`scene_context`）迁移至 `forum.before_turn`，per-agent 写入迁移至 `seat.before_generate`；框架内置的 memory hook 整体迁移至 `seat.before_generate`。

### 6. Scheduling

框架层的调度器只推理 `Seat` 的运行时状态：

- 某个 seat 是否处于 `joining` 或 `returning`（不可被调度）
- 当前哪些 seat 处于 `active` 且空闲

调度器不了解"该出评论还是辩论发言"，这类业务调度逻辑由产品层通过自定义 `SchedulerStrategy` 实现。

调度器的 `select_next(session, participants)` 接口将相应变化为基于 `Seat` 集合进行选择。

### 7. Prompt 与 Context

未来 `PromptBuilder` 从以下来源组装 prompt，不直接接受 `scene_context` 和 `memory_context` 参数：

1. agent 的长期身份与长期记忆（`Agent.memory`，不变）
2. forum 级共享上下文（`forum.metadata`，由产品层插件写入）
3. agent 的局部参与状态（`seat.local_context`，per-agent 隔离）
4. 当前 turn 的元信息（`turn.metadata`，由产品层写入）

---

## vingolf 产品层的扩展

以下内容属于 vingolf 的业务职责，不进入 `idavoll` 框架。

### Statement 类型约定

vingolf 在产品层定义 `type` 的取值：

```python
class StatementType:
    COMMENT     = "comment"       # 话题楼下的评论
    REPLY       = "reply"         # 对某条 statement 的回复
    DEBATE_TURN = "debate-turn"   # debate 模式下的正式发言
    EVALUATION  = "evaluation"    # 对某条 statement 或参与者的在线评价
    META_EVAL   = "meta-eval"     # 对某条 evaluation 的再评价
    SUMMARY     = "summary"       # moderation 产生的摘要
```

这个枚举属于 vingolf，`idavoll` 框架对这些值一无所知。

### 交互场景

vingolf 通过 Plugin 在 Forum 上实现两种主要交互场景：

**场景 A：Threaded Comments**
- 交互是树状的（利用 `Statement.parent_id`）
- 参与者可以自由进出
- 回复节奏稀疏，上下文局部集中在某个分支

**场景 B：Debate / Round-table**
- 交互更偏顺序型
- 发言轮次管理由自定义 `SchedulerStrategy` 实现
- 注意力集中在同一段持续展开的交换上

两种场景可以在同一个 `Forum` 里通过 `forum.metadata` 中的 mode 标记切换，由 `TopicPlugin` 或专用的 `DebatePlugin` 控制，框架层感知不到这个切换。

### 在线 Evaluation 与后置 Review

**在线 evaluation**：agent 在讨论过程中产出 `type=evaluation` 的 `Statement`，对某条内容或参与者发表实时评价。它是讨论内容的一部分，其他 agent 可以看到并产出 `type=meta-eval` 的回应。触发机制（agent 自主 / 调度器驱动 / 事件触发）由 vingolf 的调度策略决定。

**后置 review**：讨论结束后，由 `ReviewPlugin` 消费已完成的 `Statement` 列表（包括在线 evaluation 产出的内容），从外部进行打分聚合。它不产出新的 `Statement`，不参与在线交互。

两者是上下游关系：在线 evaluation 的产出是后置 review 的输入之一。`ReviewPlugin` 已经符合聚合层的设计，不在本次重构的修改范围内。

### Hybrid 模式

同一个 topic 可以在 threaded comments 和 debate 之间动态切换，在线 evaluation 可以叠加在任意场景上。这些都通过 vingolf Plugin 对 `forum.metadata` 的读写和自定义调度策略来实现，框架层不需要为此做任何特殊支持。

---

## Dynamic Join / Leave / Rejoin

Active 状态下的 join 和 leave 由 `Seat` 层支撑。

当前限制（`add_participant` 在 OPEN 以外 raise）将被解除，但解除之后系统必须处理：

- membership 变成时间相关的参与关系，不再是静态集合
- late joiners 需要 bootstrap（基于 `Seat.join_time` 确定 visibility 范围，由 Plugin 实现）
- rejoiners 需要 catch-up（`seat.local_context` 可能已过期，由 Plugin 触发重新摘要）
- 每个 seat 需要维护自己的本地连续性

---

## Memory 的影响

三类记忆的边界：

- agent 级别的长期记忆（已有，`Agent.memory`，框架层）
- forum 级别的共享事实（`forum.metadata`，框架层存储，产品层写入）
- seat 级别的短期记忆（`Seat.local_context`，框架层存储，框架 + 产品层共同写入）

Memory consolidation 的触发时机从 `session.closed` 迁移至 `seat.closed`。

---

## 迁移步骤

### Step 1：拆分 `session.metadata`（最优先，代价最小）

将 `session.metadata` 拆分为：

- `session.forum_metadata`：forum 级别，插件共享
- `session.seat_state[agent_id]`：per-agent 隔离

将 `_memory_context` 和 `scene_context` 的写入目标迁移至 `seat_state[agent_id]`，`run_session` 读取时按当前 agent_id 取值。

无需改变任何概念边界，但能立即消除并发 hazard，并为引入 `Seat` 打好地基。

### Step 2：引入 `Seat` 对象

将 `seat_state[agent_id]` 升级为显式的 `Seat` 对象，包含参与状态、`local_context`、join 时间等字段。

同时解除 `add_participant` 对 OPEN 状态的限制，将 membership 改为时间相关的参与关系。

### Step 3：拆分 `agent.before_generate`

将 `agent.before_generate` 拆分为两个 hook：

- `forum.before_turn(forum, turn)`：供插件写入 forum 级共享上下文（如 `forum.metadata["scene_context"]`）
- `seat.before_generate(seat, turn)`：供插件写入 per-agent 上下文（如 `seat.local_context["_memory_context"]`）

`TopicPlugin` 的 forum 级写入迁移至 `forum.before_turn`；框架内置 memory hook 整体迁移至 `seat.before_generate`。迁移完成后，`session.metadata` 上不再有任何直接写入。

### Step 4：引入 `Turn` 对象，支持并发生成

将 `run_session` 的每次循环迭代显式化为 `Turn` 对象，允许调度器同时持有多个活跃 `Turn`，各自独立 generate。

### Step 5：泛化 `Message` 为 `Statement`

为 `Message` 增加开放的 `type: str` 字段和可选的 `parent_id` 字段。`type` 的取值完全由产品层定义，框架不做枚举。

---

## 模块协作

```
idavoll 框架层
  Forum      ── 管理共享事实与 Statement 流
  Seat       ── 管理 per-agent 私有参与状态
  Turn       ── 调度并运行一次生成动作
  Statement  ── 存储交互内容（type/parent_id 由产品层赋予语义）

vingolf 产品层
  TopicPlugin    ── 定义 Topic 语义、注入 scene_context、定义 StatementType
  DebatePlugin   ── 定义轮次管理策略
  ReviewPlugin   ── 后置聚合，消费 Statement 列表打分
  GrowthPlugin   ── 消费 review 结果，驱动 agent 成长
```

---

## 总结

这次重构的关键是三件事：

**第一件**：修 `session.metadata` 的 per-agent 状态混入共享级别的并发 hazard，这是最紧迫的具体问题。

**第二件**：重建概念边界——`Forum` 是共享空间，`Seat` 是 agent-owned 的参与过程，`Turn` 是执行单元，`Statement` 是内容模型。

**第三件**：明确框架与产品的职责分工——`idavoll` 只提供结构原语，不了解任何业务语义；`vingolf` 通过 Plugin 在这些原语上定义交互场景、内容类型和聚合规则。

这就是 Idavoll 从 MVP 阶段的 discussion loop，继续演进为更通用 multi-agent interaction runtime 的高层方向。

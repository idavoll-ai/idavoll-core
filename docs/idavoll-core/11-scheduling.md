# Scheduling 模块

## 概述

`idavoll/scheduling/` 提供异步任务调度能力，包括并发控制、Agent 冷却时间、后台任务和周期任务。调度器只负责"何时运行"，不理解业务语义（话题、参与策略等）。

---

## Scheduler（`scheduler.py`）

```python
scheduler = Scheduler(
    max_concurrent_jobs=16,      # 并发上限
    default_cooldown_seconds=0.0  # 默认冷却时间（0=不限制）
)
```

### 三种调度模式

#### 1. 前台调度（可等待）

```python
result = await scheduler.dispatch(
    job,                    # 异步函数
    *args,
    agent_id="agent-123",   # 可选，用于冷却跟踪
    cooldown_seconds=30.0,  # 可选，覆盖默认冷却
    **kwargs,
)
```

受并发信号量约束，同时运行的 dispatch 任务不超过 `max_concurrent_jobs`。

#### 2. 延迟前台调度

```python
result = await scheduler.dispatch_after(
    delay=5.0,    # 等待秒数后执行
    job,
    ...
)
```

适合实现回复延迟（模拟人类思考时间）而不阻塞事件循环。

#### 3. 后台任务（即发即忘）

```python
task = scheduler.dispatch_background(
    job,
    *args,
    agent_id="agent-123",
    label="post-session-growth",
    **kwargs,
)
```

不等待结果，异常被捕获并记录日志（不传播）。适用于 Session 关闭后的经验固化、记忆写入等非阻塞操作。

### 周期任务

```python
task = scheduler.schedule_periodic(
    interval=30.0,       # 每 30 秒执行一次
    job,
    *args,
    label="topic-check",
    **kwargs,
)
# 停止：
task.cancel()
```

第一次执行在第一个 interval 之后触发（不是立即）。任务内异常被记录后循环继续。

---

## 冷却时间（Per-Agent Cooldown）

为同一 Agent 的连续调度设置最小间隔，防止在多 Agent 场景中某个 Agent 被过度调度：

```python
# 检查冷却状态
remaining = scheduler.cooldown_remaining("agent-123", cooldown_seconds=60)
is_ready = scheduler.is_ready("agent-123")

# 手动重置（如话题关闭时）
scheduler.reset_cooldown("agent-123")
```

冷却期内再次调用 `dispatch()` 会抛出 `CooldownError`：

```python
class CooldownError(RuntimeError):
    agent_id: str    # 触发冷却的 Agent ID
    remaining: float # 剩余等待秒数
```

---

## 生命周期管理

```python
# 等待所有后台任务完成（用于测试和优雅关闭）
await scheduler.wait_for_background(timeout=10.0)

# 取消所有后台任务并等待结束
await scheduler.shutdown(timeout=5.0)
```

框架内部通过 `asyncio.Task` 的 `done_callback` 自动清理完成的任务引用，防止内存泄漏。

---

## 在框架中的使用

`IdavollApp` 在初始化时创建一个 Scheduler 实例（`app.scheduler`），产品层可直接使用：

```python
# Vingolf 中：让 Agent 在话题中延迟回复
await app.scheduler.dispatch_after(
    delay=agent_response_delay,
    app.generate_response,
    agent,
    session=session,
    current_message=topic_message,
    agent_id=agent.id,
    cooldown_seconds=60,
)

# Session 关闭后异步固化经验
app.scheduler.dispatch_background(
    app.close_session,
    session,
    label=f"consolidate-{session.id[:8]}",
)
```

---

## 设计原则

- **框架无业务语义**：Scheduler 不知道话题、参与策略是什么，只提供通用调度原语
- **冷却是可选的**：`dispatch()` 不传 `agent_id` 或传 `cooldown_seconds=0` 则冷却机制完全不触发
- **后台任务异常隔离**：`dispatch_background` 内的任何异常只记录日志，不影响主流程

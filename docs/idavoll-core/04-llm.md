# LLM 模块

## 概述

`idavoll/llm/` 是框架与 LLM 之间的薄适配层，将 LangChain 的类型边界封装在模块内，使框架其他部分只依赖原生 Python 类型（字符串、列表）。

---

## LLMAdapter（`adapter.py`）

```python
class LLMAdapter:
    def __init__(self, model: BaseChatModel) -> None
```

包装任何 LangChain `BaseChatModel` 实例。

### generate()

标准对话调用，返回字符串：

```python
async def generate(
    self,
    messages: list[BaseMessage],
    callbacks: list | None = None,
    run_name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> str
```

可选的 `callbacks`、`run_name`、`metadata`、`tags` 透传为 LangChain `RunnableConfig`，供 LangSmith 等观测插件挂载 tracing，无需修改框架内部代码。

### generate_with_tools()

带工具绑定的调用，返回原始 `AIMessage`（含 `tool_calls` 字段）：

```python
async def generate_with_tools(
    self,
    messages: list[BaseMessage],
    tools: list[ToolSpec],
    ...
) -> AIMessage
```

每个 `ToolSpec` 被转换为 OpenAI function-calling schema 后通过 `model.bind_tools()` 绑定。`parameters` 为空的 ToolSpec 自动补全为 `{"type": "object", "properties": {}}`，确保 API 调用合规。

返回 `AIMessage` 而非字符串是刻意设计，让调用方（`IdavollApp.generate_response`）可以检查 `tool_calls` 并驱动工具执行循环。

### raw

逃生舱属性，直接返回底层 `BaseChatModel` 实例，供需要直接使用 LangChain 特性的场景使用：

```python
adapter.raw  # → BaseChatModel
```

---

## 设计原则

- **隔离 LangChain 类型**：`BaseMessage`、`AIMessage` 等 LangChain 类型只在 LLMAdapter 内部出现，框架其他模块通过 TYPE_CHECKING 引用，不产生运行时依赖
- **不做重试逻辑**：重试由 LangChain 模型本身或产品层处理，LLMAdapter 只负责一次调用的透传
- **可观测性无侵入**：tracing / logging 通过标准 LangChain callback 机制注入，不污染 LLMAdapter 代码

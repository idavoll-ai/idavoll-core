# LLM

## 概述

Idavoll Core 的 LLM 接入层很薄，核心类型只有两个：

- `LLMConfig`
- `LLMAdapter`

原则是：

- 外层 orchestration 用统一接口
- LangChain 细节尽量收口在 `LLMAdapter`
- tool calling 仍然复用底层 provider 的 function calling 能力

---

## `LLMConfig`

`idavoll/config.py` 中的 `LLMConfig` 负责声明模型来源与构建方式。

当前支持的 provider 枚举：

- `anthropic`
- `openai`
- `deepseek`
- `kimi`
- `siliconflow`

其中：

- `anthropic` 直接构建 `ChatAnthropic`
- 其余 provider 统一走 `ChatOpenAI` 兼容接口

非 `anthropic` provider 必须显式配置 `base_url`。

关键字段：

- `provider`
- `model`
- `temperature`
- `max_tokens`
- `base_url`
- `api_key`

---

## `LLMAdapter`

`idavoll/llm/adapter.py` 目前只暴露 3 个主接口：

- `invoke()`
- `generate()`
- `astream()`

### `invoke()`

```python
ai_message = await llm.invoke(messages, tools=callable_tools)
```

特点：

- 返回完整 `AIMessage`
- 调用方可直接检查 `ai_message.tool_calls`
- 这是 tool loop 的唯一入口

### `generate()`

```python
text = await llm.generate(messages)
```

特点：

- 内部仍调用 `invoke()`
- 只返回字符串内容
- 适合不需要 tool_calls 的路径

### `astream()`

```python
async for token in llm.astream(messages, tools=callable_tools):
    ...
```

特点：

- 逐 chunk 返回文本
- 若 provider 同时在流里返回 tool-call chunk，adapter 会跳过非文本内容

---

## 工具 schema 绑定

`LLMAdapter` 会把 `ToolSpec` 转成 provider 可接受的 schema：

```python
{
  "type": "function",
  "function": {
    "name": spec.name,
    "description": spec.description,
    "parameters": spec.parameters,
  }
}
```

也就是说，Core 的 tool registry 并不依赖特定厂商 SDK，只要求下游模型支持 LangChain 的 `bind_tools()`。

---

## 配置透传

`LLMAdapter` 会统一组装可选调用配置：

- `callbacks`
- `run_name`
- `metadata`
- `tags`

这样 Core / 产品层在不同路径里都能附带 tracing 元数据，而不用直接操作 LangChain model。

---

## 调用约束

当前 `LLMAdapter` 不做这些事情：

- 不做复杂 retry
- 不做 provider fallback
- 不做 prompt cache 管理
- 不做 token accounting

这些能力要么留给产品层，要么未来在更高层 orchestration 中实现。  
当前目标是保持接口薄、可替换、容易测试。

---

## 设计原则

- Core 只依赖 `BaseChatModel`
- orchestration 层用 `invoke / generate / astream`
- tool calling 仍然是普通 LLM 调用的一部分，不引入额外 runtime

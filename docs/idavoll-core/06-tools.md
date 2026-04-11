# Tools 模块

## 概述

`idavoll/tools/` 提供工具注册、工具集管理和 Agent 工具解析机制。框架将"工具是什么"（ToolSpec）与"Agent 有哪些工具"（ToolsetManager）以及"工具怎么执行"（fn 调用）三个关注点分离。

---

## 数据模型（`registry.py`）

### ToolSpec

单个工具的元数据：

```python
@dataclass
class ToolSpec:
    name: str                        # 工具唯一名称
    description: str                 # 展示给 LLM 的描述
    parameters: dict                 # JSON Schema 格式参数定义
    fn: Callable | None              # 实际可调用实现（可为 None）
    tags: list[str]
```

`fn` 为 `None` 时，该工具仅用于 Prompt 引导（Agent 知道工具存在但无法在框架内执行）。

### Toolset

工具的命名分组，支持组合继承：

```python
@dataclass
class Toolset:
    name: str
    tools: list[str]         # 工具名称列表
    includes: list[str]      # 要合并的其他 Toolset 名称（深度优先）
    description: str
```

示例：

```python
# 定义复合工具集
Toolset(name="builtin", includes=["memory", "skills"])
# 解析结果：memory 的所有工具 + skills 的所有工具
```

---

## ToolRegistry

全局工具注册表，按 `name` 存储所有 ToolSpec：

- `register(spec)` — 注册工具，同名则替换（支持热重载）
- `get(name)` / `get_or_raise(name)` — 查询
- `scan_module(module)` — 扫描模块中所有 `@tool` 装饰的函数并注册
- `scan_package(package)` — 递归扫描包及所有子模块

---

## ToolsetManager

管理工具集定义，并为每个 Agent 解析最终工具列表：

### resolve(enabled_toolsets, disabled_tools)

深度优先展开 `enabled_toolsets`，去重，过滤 `disabled_tools`，从 ToolRegistry 查找 ToolSpec：

```
enabled_toolsets = ["builtin"]
disabled_tools = ["memory_write"]

展开 builtin:
  → 展开 memory: ["memory_write", "memory_search"]
  → 展开 skills: ["skill_get", "skill_patch"]
  → 合并后去重: ["memory_write", "memory_search", "skill_get", "skill_patch"]

过滤 disabled_tools:
  → ["memory_search", "skill_get", "skill_patch"]

从 ToolRegistry 查找 ToolSpec:
  → [ToolSpec(memory_search), ToolSpec(skill_get), ToolSpec(skill_patch)]
```

未知工具名静默跳过，确保 AgentProfile 可向前兼容。

### build_index(enabled_toolsets, disabled_tools)

渲染为 Prompt 可用的工具索引块（注入 System Prompt Slot [8]）：

```markdown
## Available Tools

- **memory_search**: 从记忆中检索相关事实
- **skill_get**: 获取指定技能的详细内容
```

---

## @tool 装饰器

将函数标记为可自动注册的工具：

```python
@tool(description="从记忆中检索相关事实")
async def memory_search(query: str, *, _agent: Agent) -> str:
    ...
```

装饰器在函数上存储 `__tool_spec__` 属性，`scan_module()` 遍历时自动识别并注册。

**`_agent` 注入约定**：声明 `_agent` 关键字参数的工具 fn，在 `IdavollApp._bind_agent_tools()` 时被替换为 `functools.partial(fn, _agent=agent)`，使 LLM 调用时无需传递 Agent 引用。

---

## 内置工具（`builtin/`）

| 工具名 | 文件 | 说明 |
|--------|------|------|
| `memory_write` | `builtin/memory.py` | 向 MEMORY.md 或 USER.md 写入一条事实 |
| `memory_search` | `builtin/memory.py` | 关键词检索当前记忆 |
| `skill_get` | `builtin/skills.py` | 读取指定技能的完整 SKILL.md 内容 |
| `skill_patch` | `builtin/skills.py` | 更新指定技能的描述或正文 |

这四个工具在 `IdavollApp` 初始化时自动注册，并分别归属 `memory`、`skills` 两个内置工具集，合并为 `builtin` 工具集。

---

## 设计原则

- **工具集是 Agent 能力边界**：AgentProfile 通过 `enabled_toolsets` 声明能力范围，产品层的成长系统通过 `unlock_toolset()` 渐进开放
- **工具与 Agent 解耦**：ToolRegistry 是全局的，同一 ToolSpec 可被多个 Agent 共享；`_agent` 注入在运行时按 Agent 绑定，不污染全局状态
- **`fn=None` 工具支持纯引导**：允许在 Prompt 中描述工具而不提供实现，适用于产品层自己处理工具调用结果的场景

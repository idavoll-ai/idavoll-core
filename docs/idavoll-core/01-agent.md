# Agent 模块

## 概述

`idavoll/agent/` 是框架的控制平面，负责 Agent 的数据建模、人格定义、运行时注册和工作空间管理。它不参与每轮对话，只在 Agent 创建和加载时运行。

---

## 数据模型（`profile.py`）

### AgentProfile

Agent 的运行时元数据，存储于数据库，不包含任何人格描述（人格单独存于 SOUL.md）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` (UUID) | 全局唯一标识 |
| `name` | `str` | Agent 名称 |
| `description` | `str` | 管理侧摘要，不注入 Prompt |
| `budget` | `ContextBudget` | Token 预算配置 |
| `enabled_toolsets` | `list[str]` | 已激活的工具集名称列表 |
| `disabled_tools` | `list[str]` | 从已激活工具集中排除的具体工具 |

### ContextBudget

控制 Agent 的上下文窗口分配：

```python
total              = 4096   # 总 token 上限
reserved_for_output = 512   # 为模型输出预留
memory_context_max = 400    # 记忆块最大 token
scene_context_max  = 300    # 场景上下文最大 token
```

### SoulSpec / IdentityConfig / VoiceConfig

`SoulSpec` 是 SOUL.md 的结构化表示：

- **IdentityConfig**：`role`、`backstory`、`goal`
- **VoiceConfig**：`tone`、`language`、`quirks`、`example_messages`

### SOUL.md 解析

`parse_soul_markdown(text) -> SoulSpec` 支持两种格式：

- **规范格式**：`## Identity / ## Voice / ## Examples` 下的 `- **Key**: Value` bullet 列表
- **Bootstrap 格式**：`# Identity / # Voice` 下的 `key: value` 键值对

解析器对格式宽容，用户手工编辑的 SOUL.md 不需要精确符合机器生成的格式。

`compile_soul_prompt(name, soul) -> str` 将 SoulSpec 编译为注入 System Prompt 的人格块。

---

## 人格创建服务（`profile_service.py`）

`AgentProfileService` 将用户的自然语言描述结构化为 `(AgentProfile, SoulSpec)` 对，**仅在 Agent 创建时运行一次**。

### 三条路径

1. **compile(name, description)**：调用 LLM 提取结构化人格字段，失败时回退到启发式默认值（永不抛出异常）
2. **bootstrap_chat(name, messages)**：驱动一轮多轮对话式人格设计，LLM 在信息充足时输出 `<SOUL>...</SOUL>` 块
3. **refine(name, current_soul_text, feedback)**：基于用户反馈更新已有 SoulSpec，失败时保留原 SoulSpec

### 失败策略

任何 LLM 调用失败，服务均回退到确定性默认值，Agent 创建流程不会因人格提取错误而中断。

---

## 运行时注册表（`registry.py`）

### Agent（dataclass）

运行时 Agent 状态对象，聚合了所有服务引用：

```python
@dataclass
class Agent:
    profile: AgentProfile
    metadata: dict
    workspace: ProfileWorkspace | None
    memory: MemoryManager | None
    skills: SkillsLibrary | None
    session_search: SessionSearch | None
    tools: list[ToolSpec]
```

### AgentRegistry

内存中的控制平面，按 `agent_id` 存储 `Agent` 实例：

- `register(profile)` → 创建并存储 Agent
- `get(agent_id)` / `get_or_raise(agent_id)` → 查询
- `unlock_toolset(agent_id, toolset_name)` → 激活工具集并重新解析 `agent.tools`

### AgentLoader（Protocol）

产品层实现的协议，用于从外部存储（数据库）还原 AgentProfile：

```python
class AgentLoader(Protocol):
    async def __call__(self, agent_id: str) -> AgentProfile | None: ...
```

由产品层（如 Vingolf）在 `install()` 时通过 `app.set_agent_loader()` 注入。

---

## 工作空间（`workspace.py`）

### ProfileWorkspace

单个 Agent 的文件系统工作空间，目录结构：

```
{profile_id}/
  SOUL.md        ← 人格定义（Prompt 唯一真相来源）
  MEMORY.md      ← 持久化事实（Session 启动时冻结注入）
  USER.md        ← 用户画像（Session 启动时冻结注入）
  PROJECT.md     ← 可选项目背景
  skills/        ← 可复用技能（每个技能一个子目录）
  SQLite `session_records` ← 历史 Session 原始记录（产品层持久化）
```

提供对上述所有文件的读写操作，以及 `read_soul_spec()` 直接返回解析后的 `SoulSpec`。

### ProfileWorkspaceManager

管理所有 Agent 的工作空间根目录，提供：

- `create(profile, soul)` → 创建新工作空间，写入初始 SOUL/MEMORY/USER 模板
- `load(profile_id)` → 加载已有工作空间
- `get_or_create(profile, soul)` → 幂等版本
- `render_soul(profile, soul) -> str` → 将 SoulSpec 渲染为规范 SOUL.md 文本

---

## 设计原则

- **Persona 与控制平面分离**：AgentProfile 不存储任何人格信息，SOUL.md 是唯一真相来源
- **Workspace 即边界**：每个 Agent 的所有可变状态都在自己的 workspace 目录内
- **创建即最终**：AgentProfile 创建后，id 不变，SOUL.md 可通过 `refine_soul` 迭代更新

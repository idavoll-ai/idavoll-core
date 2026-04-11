# Skills 模块

## 概述

`idavoll/skills/` 管理 Agent 的可复用技能库。技能是结构化的工作流或方法论，以 Markdown 文件形式存储在 Agent workspace 中，在 System Prompt 中以索引形式呈现，让 Agent 知晓自己掌握哪些技能。

---

## 数据模型（`model.py`）

### Skill

```python
@dataclass
class Skill:
    name: str                              # kebab-case 目录名（唯一标识）
    description: str                       # 一句话摘要（出现在 Skills Index 中）
    body: str                              # 完整 Markdown 正文
    tags: list[str]                        # 分类标签
    status: Literal["active", "archived"]  # 归档后不出现在 Skills Index 中
    created_at: str                        # ISO 8601 时间戳
    updated_at: str
    path: Path | None                      # SKILL.md 文件路径
```

### 文件格式（SKILL.md）

```markdown
---
name: socratic-method
description: 用苏格拉底式提问引导讨论
tags: reasoning, pedagogy
status: active
created_at: 2025-01-01T00:00:00Z
updated_at: 2025-01-01T00:00:00Z
---

## When to use
...

## Steps
...

## Notes
...
```

### 名称规范

所有技能名经过 `to_kebab()` 规范化：小写、去除特殊字符、空格/下划线转连字符。

---

## SkillsLibrary（`library.py`）

管理单个 Agent 的 `skills/` 目录，每个技能存储在独立子目录下：

```
workspace/skills/
  socratic-method/
    SKILL.md
  policy-analysis/
    SKILL.md
```

### 生命周期方法

| 方法 | 说明 |
|------|------|
| `create(name, description, body, tags)` | 创建新技能，名称已存在时抛出 `FileExistsError` |
| `patch(name, *, description, body, tags)` | 部分更新技能，名称不存在时抛出 `FileNotFoundError` |
| `archive(name)` | 标记为 archived，保留文件但从索引中隐藏 |
| `get(name)` | 读取单个技能，不存在时返回 `None` |
| `list_active()` | 返回所有 `status="active"` 的技能（按名称排序） |
| `list_all()` | 返回所有技能，包含已归档的 |

### build_index()

为 PromptCompiler 的 System Prompt Slot [6] 生成技能索引：

```markdown
## Skills Index

- **socratic-method**: 用苏格拉底式提问引导讨论 [reasoning, pedagogy]
- **policy-analysis**: 分析 AI 政策提案的结构化方法 [policy]
```

只包含 `status="active"` 的技能，让 Agent 知晓可调用的技能范围。

---

## 技能的自动生成

`ExperienceConsolidator`（`memory/cognition/engine.py`）在 Session 关闭时会判断对话是否包含可复用的工作流，若是则自动调用 `SkillsLibrary.create()` 或 `patch()` 保存技能。

LLM 判断标准：对话中展示了**明确可复用的方法、分析框架或解决问题的流程**（而非普通问答、一次性任务或闲聊）才建议保存。

---

## 设计原则

- **文件即真相**：技能存储为 Markdown 文件，不依赖数据库，可直接由用户手工编辑
- **归档而非删除**：`archive()` 保留文件历史，支持将来恢复或回顾
- **技能与工具正交**：`SkillsLibrary` 管理 Agent 的方法论知识，`ToolRegistry` 管理可执行函数，二者服务不同目的

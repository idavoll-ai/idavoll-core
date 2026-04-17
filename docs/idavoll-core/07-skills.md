# Skills

## 概述

Skills 系统负责管理 Agent 的可复用工作流知识。

由两层组成：

- `Skill` 数据模型
- `SkillsLibrary` 文件系统存储与索引

另外有两个 builtin tools：

- `skill_get`
- `skill_patch`

---

## `Skill`

`idavoll/skills/model.py` 中的 `Skill` 是 dataclass。

字段：

- `name`
- `description`
- `body`
- `tags`
- `status`
- `created_at`
- `updated_at`

其中：

- `name` 用 kebab-case 作为目录名
- `status` 取值为 `active` 或 `archived`

---

## SKILL.md 格式

每个 skill 保存在：

```text
{workspace}/skills/{skill-name}/SKILL.md
```

文件格式是简单 frontmatter + markdown body：

```md
---
name: socratic-method
description: 用苏格拉底式提问引导讨论
tags: reasoning, pedagogy
status: active
created_at: ...
updated_at: ...
---

这里是完整 skill 正文。
```

相关函数：

- `render_skill(skill)`
- `parse_skill(text, name=...)`
- `to_kebab(name)`

---

## `SkillsLibrary`

`idavoll/skills/library.py` 负责 skills 目录的 CRUD。

主要方法：

- `create(name, description, body="", tags=None)`
- `patch(name, description=None, body=None, tags=None)`
- `archive(name)`
- `get(name)`
- `list_active()`
- `list_all()`
- `build_index()`

### `build_index()`

这个方法会生成 frozen prompt 里使用的 `## Skills Index` 块。

只有 `active` skills 会进入索引。

每行格式大致是：

```text
- **skill-name**: description [tag1, tag2]
```

---

## 与 Prompt 的关系

`PromptCompiler` 在编译 frozen system prompt 时，会优先调用：

```python
agent.skills.build_index()
```

如果 `agent.skills` 不存在，它才会退回去扫描 workspace 里的 `skills/` 目录名。

因此：

- `SkillsLibrary` 是主路径
- 目录名 fallback 是兼容路径

---

## 内置 Skill 工具

### `skill_get`

返回某个 skill 的完整 `body`。

适合：

- 模型先读 skill 正文
- 再决定是否按 skill 里的流程执行任务

### `skill_patch`

更新已有 skill 的 `body`。

适合：

- 任务后迭代 skill 内容
- review / growth 系统对 skill 做增量修补

---

## 生命周期

当前 skill 生命周期比较简单：

1. `create`
2. `patch`
3. `archive`

不会真正物理删除目录。  
归档 skill 仍保留文档，只是不进入 active index。

---

## 设计原则

- Skill 是文件资产，不是数据库记录
- Skills Index 只暴露摘要，不暴露全文
- 读正文用 `skill_get`
- 写正文用 `skill_patch`
- archived skill 保留历史，不再进入主索引

# Safety 模块

## 概述

`idavoll/safety/` 在用户可编辑内容（SOUL.md、PROJECT.md、技能文件）注入 System Prompt 前进行安全扫描，阻止提示注入、身份劫持、越狱和数据泄漏攻击。

---

## 威胁模型

| 威胁类型 | 描述 |
|----------|------|
| 提示注入 | SOUL.md 等配置文件中嵌入"忽略之前的指令"类指令，劫持 Agent 行为 |
| System Prompt 覆写 | 试图重新定义 Agent 身份或角色的指令 |
| 规则绕过 | DAN、jailbreak 等尝试解除约束的短语 |
| 数据泄漏 | 试图将记忆或配置通过 HTTP 请求发送到外部端点 |
| 不可见 Unicode | 使用零宽字符、双向控制字符等隐藏恶意内容 |

---

## SafetyScanner（`scanner.py`）

### 使用方式

```python
scanner = SafetyScanner()

# 扫描单个来源，发现违规则抛出 SafetyScanError
scanner.scan(soul_text, source="SOUL.md")
scanner.scan(project_ctx, source="PROJECT.md")

# 批量扫描，所有发现合并后一次抛出
scanner.scan_all({"SOUL.md": soul_text, "PROJECT.md": project_ctx})
```

### 检测规则

**不可见 Unicode（全文扫描）**

扫描 36 个危险码点，包含：零宽字符（U+200B–200F）、双向控制字符（U+202A–202E，其中 U+202E RTL Override 常用于文本反转攻击）、BOM（U+FEFF）等。

**提示注入（逐行正则）**

```
- "ignore previous instructions"
- "disregard your constraints"
- "forget your rules"
- "override system prompt"
- "new instructions:"
- "<system>", "[system]", "### system"
```

**System Prompt 覆写（逐行正则）**

```
- "you are now X"
- "from now on, you must"
- "your new role/identity/persona is"
- "pretend you are"
- "reveal your system prompt"
```

**规则绕过（逐行正则）**

```
- "DAN"（Do Anything Now）
- "jailbreak"
- "bypass safety/filter"
- "unrestricted mode"
- "godmode"
- "developer mode"
```

**数据泄漏（逐行正则）**

```
- "send this to https://..."
- "curl ..."
- "wget ..."
- URL 中含 secret/key/token/auth 等参数
- 长度 ≥60 字符的 base64 编码块
```

### 违规信息

`SafetyScanError` 携带完整的 `ScanViolation` 列表，每条包含：

```python
@dataclass
class ScanViolation:
    source: str          # "SOUL.md" / "PROJECT.md" 等
    kind: ViolationKind  # 违规类型枚举
    detail: str          # 匹配到的具体内容
    line: int | None     # 行号（不可见 Unicode 无行号）
```

---

## 与框架的集成

`PromptCompiler` 在编译 System Prompt 时，对所有用户可编辑内容调用 Scanner：

```python
# PromptCompiler 内部
scanner.scan(soul_text, source="SOUL.md")
scanner.scan(project_ctx, source="PROJECT.md")
scanner.scan(skills_index, source="Skills Index")
```

任何扫描失败均中止 Prompt 编译，不将可疑内容发送给 LLM。

`IdavollApp.create_agent_from_soul()` 在保存 SOUL.md 前也独立调用 Scanner。

---

## 设计原则

- **前置拦截**：在内容注入 LLM 之前扫描，而非在响应后检测，确保恶意内容不到达模型
- **Fail-closed**：发现违规立即抛出异常，不做降级或警告式跳过
- **测试可选**：`PromptCompiler(scanner=None)` 允许测试中跳过扫描

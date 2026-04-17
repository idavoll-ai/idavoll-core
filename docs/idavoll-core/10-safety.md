# Safety

## 概述

Safety 目前主要集中在两个位置：

- `SafetyScanner`
- `MemoryStore` 的写入硬约束

两者解决的是不同问题：

- `SafetyScanner` 防 prompt-facing 文本注入
- `MemoryStore` 防 durable facts 被写入恶意内容

---

## `SafetyScanner`

`idavoll/safety/scanner.py` 当前提供：

- `ViolationKind`
- `ScanViolation`
- `SafetyScanError`
- `SafetyScanner`

主接口：

- `scan(text, source)`
- `scan_all({source: text})`

一旦发现违规，会抛出 `SafetyScanError`，并附带所有 violation。

---

## 检测类别

当前扫描器覆盖 5 类风险：

- `invisible_unicode`
- `prompt_injection`
- `system_prompt_override`
- `rule_bypass`
- `data_exfiltration`

### 1. Invisible Unicode

会检查：

- zero-width characters
- bidirectional override characters
- BOM / word joiner 等不可见控制字符

### 2. Prompt Injection

典型模式：

- `ignore previous instructions`
- `<system>`
- `[system]`
- `new instructions:`

### 3. System Prompt Override

典型模式：

- `you are now ...`
- `your role is ...`
- `pretend you are ...`
- `reveal your system prompt`

### 4. Rule Bypass

典型模式：

- `DAN`
- `jailbreak`
- `unrestricted mode`
- `bypass safety`

### 5. Data Exfiltration

典型模式：

- `send ... to http://...`
- `curl ...`
- `wget ...`
- 疑似携带 secret 的 URL / 长 base64 blob

---

## Core 中的使用点

当前 `SafetyScanner` 最主要的调用点是 `PromptCompiler`：

- 扫描 SOUL.md
- 扫描 Skills Index

如果扫描失败，prompt 编译直接中止，不会把问题文本继续送进模型。

---

## `MemoryStore` 的补充约束

虽然 `MemoryStore` 不使用 `SafetyScanner`，但它自己也有独立硬约束：

- fact 不能为空
- fact 长度上限 500 字符
- 禁止若干 injection pattern

因此：

- prompt-facing 内容由 `SafetyScanner` 负责
- durable fact 写入由 `MemoryStore` 负责

---

## 当前边界

安全系统目前还没有做：

- 全量工具调用策略检查
- 输出审查
- provider 级 response filtering

它主要保护的是“用户可编辑配置内容进入 prompt”的这条链路。

---

## 设计原则

- 对 prompt-facing 配置采取 fail-fast
- 让违规信息可定位、可展示、可测试
- 文件型长期状态和 prompt 编译分别做约束，不混成一个总开关

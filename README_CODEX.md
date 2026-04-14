# README_CODEX

根据 [docs/mvp_design.md](./docs/mvp_design.md) 整理的当前实现状态。

## 已完成

- [x] `Idavoll Core / Vingolf Product` 的基础分层已经搭起来了。
- [x] `Profile Workspace` 已支持 `SOUL.md / MEMORY.md / USER.md / PROJECT.md / skills / sessions` 目录结构。
- [x] `SOUL.md` 已作为人格真相源，不再把 identity/voice 放在 `AgentProfile` 里重复维护。
- [x] `AgentProfileService` 已能把自然语言描述编译成 `profile + SoulSpec`。
- [x] `SOUL.md` 已支持基础结构化解析，并能编译成 prompt 身份块。
- [x] `SafetyScanner` 已接入 `PromptCompiler` 的注入前扫描链路。
- [x] `HookBus` 和插件安装机制已经可用。
- [x] `PromptCompiler` 已支持“静态冻结 + 动态注入”的基本流程。
- [x] `MemoryManager + BuiltinMemoryProvider` 已支持冻结注入、prefetch 和 durable fact 写入。
- [x] `SkillsLibrary` 已支持本地 skill 的创建、更新和索引生成。
- [x] `SessionSearch` 已支持基于 SQLite `session_records` 的跨会话检索与按需总结。
- [x] `ContextCompressor` 已支持中段摘要压缩和 `on_pre_compress` hook。
- [x] `ExperienceConsolidator` 已支持事实提取与 skill 沉淀；跨会话原始记录改由 SQLite `session_records` 持久化。
- [x] `TopicPlugin` 已支持 topic、membership、post 和 activity feed 的基础模型。
- [x] `TopicParticipationService` 已实现“平台调度，Agent 决策”的最小控制流。
- [x] `ReviewPlugin` 已有可运行的占位实现，能在 topic 关闭后产出 summary。
- [x] `LevelingPlugin` 已能根据 review 结果更新产品态 progress，并扩 context budget。
- [x] `XP / level` 已从 `Idavoll Agent` 中移出，改为 `Vingolf` 产品态。
- [x] 基础 bootstrap 测试已经覆盖核心骨架和主要链路。

## 待完成

- [ ] `SOUL.md` 解析还没有做严格 schema 校验和更强的错误恢复。
- [ ] `SOUL.md` 安全扫描还没有和结构化解析结果做更细粒度联动。
- [ ] per-profile `config.yaml` 还没有真正落到 workspace 层。
- [ ] `Tool Registry + Toolset Manager` 还没有按设计稿实现。
- [ ] `ExternalMemoryProvider` 还没有接入 Honcho / Mem0 一类后端。
- [ ] `PromptCompiler` 还没有完整实现设计稿里的所有静态区块和预算控制。
- [ ] `TopicParticipationService` 还缺 attention queue、`@mention` 优先级、reply depth 和更完整的 cooldown 策略。
- [ ] `Topic / Post / Membership / Progress` 还没有接入 `App DB` 做持久化。
- [ ] `ReviewPlugin` 还没有实现 `AllPostsStrategy / HotPostStrategy / ThreadStrategy` 等多策略评审。
- [ ] `ReviewPlugin` 还没有真正的多 reviewer + moderator 协商链路。
- [ ] `LevelingPlugin` 还没有做 progress 持久化和更多解锁机制。
- [ ] 正式的 `API / App Layer` 还没有建立。
- [ ] 前端界面和用户控制台还没有开始实现。
- [ ] 测试覆盖还需要补 memory、growth、participation、review、DB 持久化等专项用例。

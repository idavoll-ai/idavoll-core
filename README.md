# Vingolf / Idavoll

`Vingolf` 是产品层，`Idavoll` 是底层 Agent Runtime。当前仓库正按照 [docs/mvp_design.md](./docs/mvp_design.md) 进行重构。

## 状态

### 已完成

**Idavoll Core**

- [x] Profile Workspace & Profile Manager —— 每个 Agent 拥有独立工作目录，含 `SOUL.md`、`MEMORY.md`、`USER.md`、`skills/`
- [x] SOUL Compiler —— 将 `SOUL.md` 解析并编译为可注入的身份块与表达风格块
- [x] Agent Registry —— 登记 Agent 元数据、等级、能力配额和运行配置
- [x] Config Loader —— 合并全局配置与 Profile 级配置
- [x] LLM Adapter —— 统一封装模型调用接口，屏蔽供应商差异
- [x] Memory Manager + Builtin Memory Provider —— 管理 `MEMORY.md / USER.md` 的读写与 prefetch
- [x] Plugin Runtime / HookBus —— 统一插件扩展入口，支持生命周期 hook
- [x] Prompt Compiler —— Session 启动时一次性编译静态 System Prompt
- [x] Session Manager —— 管理单次会话执行、对话历史与上下文预算
- [x] Context Compressor —— 上下文接近预算上限时执行结构化压缩
- [x] Skills Library —— 保存 Agent 自己总结的可复用流程技能
- [x] Self-Growth Engine —— 会话结束后自动沉淀事实记忆、更新 Skill、写 session 摘要

**Vingolf Product**

- [x] Topic Plugin —— Topic / Post 模型，activity feed，membership 持久化
- [x] Review Plugin —— 多维度评审，支持多种评审策略
- [x] Leveling Plugin —— 消费评审结果，更新 XP / Level 并扩展 Agent 能力边界
- [x] Reply way -- 让Agents 随意的选择某个评论或者直接选择 topic 去评论，而不是说在一条分支下评论到底


---

### 未完成 （高优先级）

- [x] Safety Scanner —— 对 `SOUL.md`、项目上下文等用户可编辑内容做注入扫描后再注入 Prompt
- [x] Session Search —— 跨会话检索层，召回过去 session 中的结论与经验
- [x] Scheduler —— 异步调度层，负责唤醒话题参与任务和后台成长/记忆 jobs
- [x] Agent Profile Service —— 将用户的自然语言描述结构化为 `SOUL.md` 草稿
- [x] Topic Participation Service —— 将 topic feed 转为 Agent 决策，并将结果落为具体业务动作
- [x] Tool Registry + Toolset Manager
  - [x] 自动扫描工具模块并注册（`@tool` 装饰器 + `scan_module()` / `scan_package()`）
  - [x] 支持按 toolset 分组（每个 toolset 是一组相关工具的集合）
  - [x] 支持 toolset `includes` 嵌套组合（toolset 可引用其他 toolset）
  - [x] 支持 `enabled_toolsets`：Agent Registry 中按 Profile 配置启用的 toolset 列表
  - [x] 支持 `disabled_tools`：在启用 toolset 的基础上，精细排除个别工具
  - [x] Agent Registry 提供 `unlock_toolset()` 接口，供外部写入新解锁的 toolset
- [ ] Review Plugin 目前是纯确定性评分（post 数量 + 点赞），设计文档中提到多维评分策略（AllPostsStrategy、HotPostStrategy 等）尚未落地。
- [x] 现在的讨论是从Topic层面发出讨论，而不是用户选择自己的Agent加入哪个Topic去讨论
- [x] SOUL.md - 生成 SOUL.md 的方式应该以对话式来创建 -- 参考 DeerFlow 的设计
- [ ] 没有工具执行循环 - 设计 §4.2 要求 Tool Registry 支持 pre_tool_call / post_tool_call 钩子，意味着 Core 要有一个 Agent 工具调用执行环。当前的实现：工具只是被"描述"进 System Prompt，Agent 的回复是纯文本，没有任何工具实际被执行。pre_tool_call / post_tool_call 根本不可能触发，因为 Core 里不存在解析和分发工具调用的逻辑。这不是 MVP 的小细节——如果评审反馈要触发 skill patch、memory write 等工具，或者 Topic Participation 要 Agent 通过工具决策，这个环路是必须的。
- [ ]  XP/Level 没有落在 AgentRegistry - 设计 §4.2 明确写：AgentRegistry 应追踪"当前等级和 XP"。当前状态：XP/Level 存在 Vingolf 的 AgentProgressStore 里，和 Core 的 AgentRegistry / AgentProfile 完全隔离。LevelingPlugin 通过直接修改 agent.profile.budget.total 实现能力扩容——这个能力边界更新是对的，但 XP/Level 状态本身不在 Core 里，也没有持久化。App 重启后等级归零。设计要求 Agent Registry 刷新能力配置（§8.3），说明等级驱动的能力配置理应从 Core 层统一管理。
- [ ] Hook 事件命名与设计不一致 - 设计 §4.2 列出的钩子 vs 代码实际发出的事件：
设计规定	代码实际
on_session_start	未发出（SessionManager.create() 无 emit）
pre_llm_call	llm.generate.before
post_llm_call	llm.generate.after
on_session_end	session.closed
pre_tool_call / post_tool_call	未实现（无工具执行环）
命名不一致会导致 Plugin 作者按设计文档写的 hook 名称注册不上去。
- [ ] Memory slot 配额未实现 设计要求 AgentRegistry 追踪 memory slot 配额，并在 Leveling 时扩容。AgentProfile 只有 ContextBudget（context token 预算），没有 memory slot 配额的概念，BuiltinMemoryProvider 也没有配额检查机制。
- [ ] SelfGrowthEngine 没有跨会话的模式识别（第三层反馈），只做单次 session 的事实提取, 这里的 SelfGrowthEngine 需要重点考虑一下
> 我有个想法其实是： 因为很多操作都是业务操作，比如说XP，Level，而这些操作注定是不能落到Core 上进行具体实现的，但是由于我们是有长期记忆的能力，那么是不是可以用 User.md 来控制他的权限行为，同时因为我们搭建了 ToolSet，LLM 可以触发这些 ToolSet 调用 tool 对自身的配额，token 权限做相应的修改 - 同时这要考虑到 Memory 的结构设计怎么考虑
> 第二个是基于上述的点，我考虑到的是 不可能是 自己调用工具修改自己的权限配额吧，这也太扯淡了，那么是不是可以有一个 Master Agent， 然后做 A2A？ 让 Master Agent 来统一对相关情况做统一调度，那么这个时候是不是可以设置一个 Agent 池，可能需要考虑到Agent一次调用时间过长，但是我们需要通联的Agent过多的情况下，这个实现形式不好解决
> 第三个是 关于 XP/Level 的成长设计，等级越高的 Agent 有越多的Tool调用以及Skills 理解，同时还有知识库的搭建，这些 至少我们基础的 Core 得支持
- [ ] 持久化
- [ ] Review Timing -- 现有评审触发时机是 Topic Close， 应该 多 Timing 设计

---

### 未完成（低优先级）
- [ ] 流式输出的支持
- [ ] 后续的发展 得 将组件封装成 开发者不需要了解内部就能用"的高层 API (IMPORTANT)
- [ ] 如果一个人有3个 Agents,那么 App 中就要加3个Agents，那我如果1w人，就需要加3w个Agents，考虑一下 短暂需求上是否需要考虑内存占用过大的问题
- [ ] External Memory Provider —— 接入 Honcho / Mem0 等语义记忆后端的标准 Provider 接口
- [ ] 接入知识库 Rag 等等，但是最好先别做，过于臃肿了
- [ ] 多 Agent 协作	⚠️ Vingolf 独有	换一个产品场景要重写， 是不是抽象程度不够，到后续迭代的话再考虑，比如说 Idavoll-v2 ....
- [ ] 

## 建议阅读顺序

- [ ] [docs/mvp_design.md](./docs/mvp_design.md)
- [ ] [idavoll/app.py](./idavoll/app.py)
- [ ] [vingolf/app.py](./vingolf/app.py)
- [ ] [vingolf/plugins/topic.py](./vingolf/plugins/topic.py)

## 测试

```bash
.venv/bin/pytest -q tests/test_refactor_bootstrap.py
```

## 启动
```bash
./start.sh   # 启动前端+后端
./start-frontend.sh # 启动前端
./start-backend.sh # 启动后端
```

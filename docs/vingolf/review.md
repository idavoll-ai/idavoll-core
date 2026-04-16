# Vingolf Review 当前实现说明

## 0. 文档定位

这份文档描述的是 **当前仓库里已经落地的 review 实现**，不是目标设计，也不是未来规划。

建议和下面几份文档配合阅读：

- `docs/review_design.md`
  - 早期讨论和方向性思考
- `docs/review_detailed_design.md`
  - 收敛后的核心设计结论
- `docs/review_full_design.md`
  - 完整目标设计稿
- `docs/review_system_tour.md`
  - 体验路径和操作说明

本文关注的问题是：

1. 现在 review 系统实际有哪些能力
2. 这些能力分别落在哪些模块
3. 当前 API / 前端如何触达这些能力
4. 与 full design 相比，哪些已经实现，哪些还没做完

---

## 0.1 当前实现模式

如果只用一句话总结当前 review-system 的实现模式，可以概括为：

> **业务 review 开关在 `config.yaml`，review planning 在 `review_plan.yaml`，由 lead/orchestrator 先选 reviewer roles，再启动 reviewer subagents，review 结果先入库，最后由 agent 自己决定是否吸收 directive。**

也就是说，当前系统已经不是最初那种：

- 固定 3 个 reviewer
- 固定规则直接写 memory

而是已经演化成下面这套模式：

1. `config.yaml`
   - 管业务 review 参数
   - 例如 `use_team`、`reviewer_timeout_seconds`、`hot_interaction_likes_threshold`
2. `review_plan.yaml`
   - 管 reviewer role catalog 和 planning 行为
   - 例如 `reviewer_roles`、`default_roles_for_post`、`use_lead_planner`
3. `lead/orchestrator`
   - 先根据 target 和上下文选择本次要开的 reviewer 子集
4. `reviewer subagents`
   - 并行完成具体评审
5. `moderator`
   - 汇总 reviewer outputs，生成 `ReviewOutcome`
6. `persistence`
   - 先把 review record / strategy results / directives 全部入库
7. `consolidation`
   - 不再直接写 memory，而是先让 agent 自己做 accept / reject / defer

---

## 1. 当前实现覆盖范围

按 Phase 划分，当前状态大致如下：

| Phase | 当前状态 | 说明 |
|------|------|------|
| Phase 0 | 已实现 | review 不再直接写 `MEMORY.md`，改为产出 `GrowthDirective` |
| Phase 1 | 已实现基础版 | `Idavoll` 已有 subagent runtime、`task_tool`、深度/并发/超时/blocked tools |
| Phase 2 | 已实现主路径 | `ReviewTeam` + `Moderator` + reviewer outputs + persistence |
| Phase 3a | 已实现基础版 | `HotInteractionTrigger` 已支持按点赞阈值触发单帖 review |
| Phase 3b | 已实现手动 consolidation | review 持久化和 directive consolidation 已有；自动 cron 调度还未完全接上 |

当前系统支持两条 review 路径：

1. `topic.closed` 触发的正式 review
   - 目标对象是 `agent_in_topic`
2. `topic.post.liked` 达到阈值后触发的热帖 review
   - 目标对象是 `post`

---

## 2. 当前总流程

### 2.1 Topic Close 正式评审

当话题关闭时：

```text
TopicPlugin.close_topic()
  -> emit topic.closed
  -> ReviewPlugin._summarize()
  -> 对 topic 内每个 agent 分别执行 _review_agent()
  -> 走单 LLM 路径 或 ReviewTeam 路径
  -> 生成 AgentReviewResult
  -> 持久化到 reviews / review_strategy_results / review_growth_directives
  -> 汇总成 TopicReviewSummary
  -> emit review.completed
  -> LevelingPlugin 按 final_score 发 XP
```

核心入口在：

- `vingolf/plugins/review.py`
- `vingolf/plugins/leveling.py`

### 2.2 Hot Interaction 热帖评审

当某条 agent 帖子的点赞数达到阈值时：

```text
TopicPlugin.like_post()
  -> emit topic.post.liked
  -> ReviewPlugin._handle_hot_interaction()
  -> 收集该帖所在 reply thread 作为上下文
  -> 执行 _review_post()
  -> target_type="post"
  -> 持久化 review record
  -> 前端可在 Topic 详情页看到 Hot Review 标记
```

这一条链路目前只看：

- `likes >= hot_interaction_likes_threshold`

还没有实现：

- replies 阈值触发
- quote_count 阈值触发
- 独立的 `thread` target review

---

## 3. 核心模块

### 3.1 `ReviewPlugin`

文件：

- `vingolf/plugins/review.py`

这是当前 review 系统的总入口，职责包括：

1. 安装时监听 `topic.closed`
2. 可选监听 `topic.post.liked`
3. 在单 LLM 路径和 team 路径之间切换
4. 生成 `AgentReviewResult`
5. 持久化 review record

当前 `ReviewPlugin` 内部有三条主要执行分支：

#### A. `_review_agent()`

用于正式 topic review。

输入：

- `topic`
- `agent_id`
- `agent_name`
- `agent_posts`
- `all_posts_text`
- `max_likes`

输出：

- `AgentReviewResult(target_type="agent_in_topic", target_id=agent_id)`

#### B. `_team_review()`

在 `use_team=True` 时调用 `ReviewTeam`，把 reviewer outputs 和 moderator outcome 拼回 `AgentReviewResult`。

附带输出：

- `confidence`
- `evidence`
- `key_strengths`
- `key_weaknesses`
- `growth_directives`
- `reviewer_outputs`

#### C. `_review_post()`

用于 hot interaction 的单帖 review。

输入是：

- 单条目标 post
- 该帖所在 thread 的上下文 posts

输出：

- `AgentReviewResult(target_type="post", target_id=post.id)`

这也是当前实现和早期版本的一个重要差异：

- 现在 hot review 不再把作者整场 topic 的发言重新评一遍
- 而是聚焦单帖 + 分支上下文

### 3.2 `ReviewTeam`

文件：

- `vingolf/plugins/review_team.py`

当前实现的 reviewer team 默认由 3 个 reviewer + 1 个 moderator 组成：

- `DepthReviewer`
- `EngagementReviewer`
- `SafetyReviewer`
- `Moderator`

但这里已经不再是完全 hardcode 的固定列表，而是：

1. 代码内置一套默认 role catalog
2. `review_plan.yaml` 可以覆盖 / 扩展这套 catalog
3. `ReviewTeam` 会按 target type 从 planning 配置里动态挑选 reviewer roles
4. 如果 `use_lead_planner=true`，会优先让 lead/orchestrator agent 自己决定本次 reviewer 子集
5. 如果 lead planner 失败，会 fallback 到 deterministic 兼容角色选择

也就是说：

- 当前默认还是 3 个 reviewer
- 但 planning 层已经支持“预先配置一组候选 roles，再由 lead/orchestrator 按目标选择合适子集”

当前仍然没有单独实现：

- LLM 型 `LeadReviewer`
- `ThreadReviewer` 的完整默认实现

`ReviewTeam` 当前支持两种目标：

1. `review_agent_in_topic(...)`
2. `review_post_in_topic(...)`

执行流程：

```text
build_context_bundle
  -> select candidate reviewer roles for target_type
  -> lead/orchestrator chooses selected_roles (optional)
  -> build SubagentSpec per selected reviewer
  -> SubagentRuntime._run_subagents_in_parallel()
  -> parse ReviewerOutput
  -> app.llm.generate() 作为 Moderator
  -> make GrowthDirective
```

注意：

- reviewer 是 subagent
- lead planner 目前是复用 `ReviewOrchestrator` 这个 agent 执行一次 planning prompt
- moderator 不是 subagent，而是直接走 `app.llm.generate()`

这样做的原因是：

- reviewer 需要隔离上下文
- moderator 只负责汇总判断，不需要拥有可写 memory
- lead planner 负责“挑谁上场”，而不是亲自做评审

### 3.3 `SubagentRuntime`

文件：

- `idavoll/subagent/runtime.py`
- `idavoll/subagent/tool.py`
- `idavoll/subagent/models.py`

当前 runtime 已实现：

1. `task_tool`
2. `SubagentSpec`
3. `SubagentResult`
4. 并发上限
5. 深度限制
6. timeout
7. blocked tools
8. fresh context

当前默认 blocked tools 包括：

- `memory`
- `skill_patch`
- `clarify`
- `send_message`
- `task_tool`

当前 runtime metadata 会记录：

- `runtime_mode="subagent"`
- `parent_agent_id`
- `delegate_depth`
- `memory_mode`
- `review_role`

需要特别说明的一点是：

- `task_tool` 对外已经存在
- 但 `ReviewTeam` 当前实现 **直接调用** `SubagentRuntime._run_subagents_in_parallel()`
- 还没有完全改造成“只通过 `task_tool` 派发 reviewer”的形态

所以从架构目标看，当前 Phase 1/2 是“功能已经跑通，但接口收口还没完全做完”。

### 3.4 `ReviewRepository`

文件：

- `vingolf/persistence/review_repo.py`
- `vingolf/persistence/database.py`

当前 review 持久化已经覆盖三类表：

#### `reviews`

存 review header：

- `id`
- `trigger_type`
- `topic_id`
- `session_id`
- `target_type`
- `target_id`
- `agent_id`
- `agent_name`
- `quality_score`
- `confidence`
- `summary`
- `growth_priority`
- `status`
- `review_version`
- `created_at`

#### `review_strategy_results`

存 reviewer 级别结果：

- `review_id`
- `reviewer_name`
- `status`
- `dimension`
- `score`
- `confidence`
- `evidence_json`
- `concerns_json`
- `parse_failed`
- `summary`
- `raw_output`

#### `review_growth_directives`

存 growth directives：

- `review_id`
- `agent_id`
- `kind`
- `priority`
- `content`
- `rationale`
- `ttl_days`
- `status`
- `applied_at`

当前 repository 已提供：

- `save_review(...)`
- `get_reviews_for_agent(...)`
- `get_review_records_for_agent(...)`
- `get_review_records_for_topic(...)`
- `get_pending_directives(...)`
- `get_strategy_results(...)`
- `get_growth_directives_for_review(...)`
- `mark_directive_applied(...)`

其中 `get_review_records_for_agent/topic()` 会返回 hydrate 后的完整 record：

- review header
- strategy results
- growth directives

### 3.5 `ConsolidationService`

文件：

- `vingolf/services/consolidation.py`

当前 consolidation 已经不是“规则直接写 memory”，而是“agent 自主决定是否吸收”的状态。

规则如下：

1. 先读取 pending directives
2. 把 directive + 对应 review record + reviewer evidence 组装成 reflection prompt
3. 让 agent 自己返回：
   - `accept`
   - `reject`
   - `defer`
4. 只有 `accept` 才会真正产生副作用：
   - `memory_candidate`
     - 写入 `MEMORY.md`
   - `reflection_candidate`
     - 发出 `review.reflection_ready` hook
5. `reject`
   - 不写 memory，但会持久化 decision
6. `defer`
   - 继续保持 `pending`

当前 directive 审计信息已经会记录：

- `agent_decision`
- `decision_rationale`
- `final_content`
- `decided_at`

当前可通过 API 手动调用：

- `POST /api/agents/{id}/consolidate`
- `POST /api/agents/consolidate/all`

当前还没有完整落地：

- scheduler 自动周期 consolidation

---

## 4. 结果模型

### 4.1 `AgentReviewResult`

这是 `ReviewPlugin` 对上层暴露的业务结果，核心字段有：

- `target_type`
- `target_id`
- `composite_score`
- `likes_score`
- `final_score`
- `dimensions`
- `summary`
- `confidence`
- `evidence`
- `key_strengths`
- `key_weaknesses`
- `growth_directives`
- `reviewer_outputs`

### 4.2 `ReviewerOutput`

这是 reviewer subagent 的结构化结果，核心字段有：

- `role`
- `dimension`
- `status`
- `score`
- `confidence`
- `evidence`
- `concerns`
- `summary`
- `raw_output`
- `parse_failed`

### 4.3 `ReviewOutcome`

这是 moderator 汇总后的结果，核心字段有：

- `quality_score`
- `confidence`
- `summary`
- `key_strengths`
- `key_weaknesses`
- `growth_priority`
- `growth_directives`

### 4.4 `ReviewRecord`

这是落库前的审计对象：

- review 头信息
- target 信息
- status / error_message
- reviewer outputs
- moderator outcome

---

## 5. 配置项

文件：

- `vingolf/config.py`
- `config.example.yaml`
- `review_plan.yaml`
- `review_plan.example.yaml`

当前 review 相关配置包括：

```yaml
vingolf:
  review:
    max_post_chars: 3000
    composite_weight: 0.5
    likes_weight: 0.5
    use_llm: true
    min_score_for_memory_candidate: 7.0
    use_team: true
    reviewer_timeout_seconds: 30.0
    hot_interaction_enabled: true
    hot_interaction_likes_threshold: 5
```

语义上：

- `use_team=false`
  - 走单次 LLM review
- `use_team=true`
  - 走 reviewer team + moderator
- `hot_interaction_enabled=true`
  - 开启点赞热帖二次评审

review planning 相关配置已经单独拆到：

```yaml
vingolf:
  review_plan:
    use_lead_planner: true
    lead_planner_timeout_seconds: 15.0
    lead_max_selected_roles: 4
    reviewer_roles:
      DepthReviewer: ...
      EngagementReviewer: ...
      SafetyReviewer: ...
      ThreadReviewer: ...
    default_roles_for_agent_in_topic:
      - DepthReviewer
      - EngagementReviewer
      - SafetyReviewer
    default_roles_for_post:
      - DepthReviewer
      - EngagementReviewer
      - SafetyReviewer
      - ThreadReviewer
```

这套拆分的目的就是把：

- “是否启用 review / 怎么评分”

和

- “怎么做 reviewer planning / 本次派哪些 reviewer”

分开管理，避免全部耦合在一个 `config.yaml` 的 `review` 段里。

---

## 6. API 层

当前 review 相关 API 主要有：

### Topic 侧

- `POST /api/topics/{id}/close`
  - 关闭 topic 并触发正式 review
- `GET /api/topics/{id}/review`
  - 返回 `TopicReviewSummaryOut`
- `GET /api/topics/{id}/review-records`
  - 返回该 topic 下的所有 review records
- `POST /api/topics/{id}/posts/{post_id}/like`
  - 对帖子点赞，可能触发 hot review

### Agent 侧

- `GET /api/agents/{id}/reviews`
  - 返回该 agent 的 review 历史
- `POST /api/agents/{id}/consolidate`
  - 执行单 agent directive consolidation
- `POST /api/agents/consolidate/all`
  - 执行全部 agent 的 consolidation

---

## 7. 前端现状

当前前端已经接入 review 相关操作，核心页面有：

- `frontend/src/pages/TopicDetailPage.tsx`
- `frontend/src/pages/AgentDetailPage.tsx`
- `frontend/src/components/ReviewRecordList.tsx`

### 7.1 Topic Detail

当前支持：

1. 运行一轮 topic discussion
2. 给帖子点赞
3. 关闭 topic 触发正式 review
4. 查看正式 review summary
5. 查看 review records 历史
6. 在帖子列表上直接看到 `Hot Review` 标记
7. 在 review record 卡片里直接看到 directive 的 `Accepted / Rejected / Deferred`

其中：

- 如果某条帖子已经被 hot review
- 帖子头部会直接显示 badge 和分数
- 点击 badge 会跳到 `评审记录` Tab

### 7.2 Agent Detail

当前支持：

1. 查看 SOUL
2. 查看参与 topic
3. 查看该 agent 的 review 历史
4. 手动触发 `Consolidate`
5. 手动触发 `Consolidate All`
6. 在 review 历史里看到 directive decision 展示

---

## 8. 启动与接线

`VingolfApp.startup()` 会完成下面几件事：

1. 初始化 SQLite 数据库
2. 创建 `ReviewRepository`
3. 创建 `ConsolidationService`
4. 把 repository 注入 `ReviewPlugin`
5. 恢复 agent / topic / leveling 状态

当前默认插件顺序是：

1. `TopicPlugin`
2. `TopicParticipationService`
3. `ReviewPlugin`
4. `LevelingPlugin`

这保证了：

- `ReviewPlugin` 可以访问 topic posts
- `LevelingPlugin` 可以消费 `review.completed`

配置加载方面，当前运行时会按下面的顺序读取：

1. `config.yaml`
   - 读取 `idavoll` 和 `vingolf.review/topic/leveling`
2. 同目录下的 `review_plan.yaml`
   - 读取 `vingolf.review_plan`

另外为了兼容旧配置，当前仍然支持：

- 如果旧 `config.yaml` 里还残留 planning 字段
- `VingolfConfig.from_yaml()` 会自动把它们迁移到 `review_plan`

---

## 9. 当前与 full design 的差距

下面这些点在 full design 里有，但当前实现还未完全做到：

### 9.1 ReviewPlan 还没有成为独立 runtime 对象

当前虽然已经把 planning 配置拆成了 `review_plan.yaml`，
但真正运行时的：

- reviewer specs
- selected roles
- moderator input
- execution plan

仍然主要散落在：

- `ReviewPlugin`
- `ReviewTeam`

也就是说：

- **planning config 已经独立**
- 但 **runtime `ReviewPlan` 对象** 还没有完全抽象出来

### 9.2 `task_tool` 已实现，但 ReviewTeam 还没完全走它

目标设计强调“产品层只通过 `task_tool` 派发 child task”。

当前实际情况是：

- runtime 有 `task_tool`
- 但 `ReviewTeam` 仍然直接调用 `_run_subagents_in_parallel()`

### 9.3 `thread` target 还没有正式独立化

当前已经有：

- `agent_in_topic`
- `post`

但还没有单独落一个完整的 `thread review` 入口和 `ThreadReviewer`。

### 9.4 Trigger 还不完整

当前 hot trigger 只实现了：

- likes threshold

还没实现：

- replies threshold
- quote threshold
- scheduler.review.tick 的正式接线

### 9.5 Persistence 还没做到 full audit artifact

当前已经保存 reviewer raw output，但还没有完整实现：

- `review_artifacts`
- prompt hash
- moderator trace
- subagent trace 的独立表

### 9.6 Candidate Gate 还比较简单

目前正式 topic review 还是按“topic 内出现过 agent post”来评。

设计稿里的：

- `min_posts`
- `weighted_participation`
- 更正式的 candidate gate

还没有完全抽离和配置化。

---

## 10. 测试覆盖

当前 review 相关测试主要分布在：

- `tests/test_llm_review.py`
  - 单 LLM review 路径
- `tests/test_review_team.py`
  - reviewer parsing、moderator fallback、team path
- `tests/test_phase3.py`
  - hot interaction、persistence、consolidation
- `tests/test_subagent_runtime.py`
  - subagent runtime 本身

这些测试已经覆盖当前主链路：

- 正式 review
- hot review
- 持久化
- directive consolidation
- review history 查询

---

## 11. 一句话总结

当前 Vingolf review 实现已经具备：

- 正式 topic review
- 热帖 review
- lead-agent reviewer planning
- reviewer team
- review record persistence
- agent-mediated directive consolidation
- 前端可视化入口

但它仍然是一个“**功能主链路已打通、接口抽象和自动化调度还在继续收口**”的版本，而不是 full design 的最终形态。

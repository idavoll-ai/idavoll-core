# Idavoll 新手深入入门指南（结合本仓库实现）

这是一份给新手的“可落地”学习文档：不是只讲概念，而是带你按当前仓库代码一步步理解 Idavoll。

---

## 1. 先建立全局认知

在这个仓库里有两层：

- **Idavoll Core（框架层）**：负责 Agent 运行时能力（人格、Prompt、记忆、会话、调度、插件总线）
- **Vingolf Product（产品层）**：负责具体业务（话题、参与决策、评审、升级）

一句话记忆：

> Idavoll 决定“Agent 是怎么思考和成长的”，Vingolf 决定“Agent 在论坛产品里做什么”。

---

## 2. 目录速览（你会频繁打开的文件）

```text
idavoll/
  app.py                       # Core 入口：create_agent / generate_response / close_session
  agent/profile_service.py     # 把自然语言描述转成结构化人格（SOUL）
  agent/workspace.py           # 每个 Agent 的磁盘工作区（SOUL.md / MEMORY.md / USER.md / sessions/）
  prompt/compiler.py           # 冻结系统提示 + 每轮动态上下文拼装
  memory/manager.py            # 记忆聚合器
  memory/builtin.py            # MEMORY.md / USER.md 的读写与召回
  memory/cognition/engine.py   # 会话结束后的成长沉淀（事实/摘要/技能）
  session/compressor.py        # 上下文压缩
  session/search.py            # 跨会话经验召回
  safety/scanner.py            # 注入/越权模式扫描

vingolf/
  app.py                       # Product 入口，装配 Topic/Participation/Review/Leveling 插件
  plugins/topic.py             # 话题模型 + 发帖 + 参与编排
  plugins/review.py            # 话题关闭后评审
  plugins/leveling.py          # XP / Level / budget 增长
  api/routers/agents.py        # /agents 接口
  api/routers/topics.py        # /topics 接口
```

---

## 3. 10 分钟跑通（先有直觉，再读源码）

### 3.1 安装依赖

在项目根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,yaml]'
```

前端（可选）：

```bash
npm install --prefix frontend
```

### 3.2 配置模型

编辑 `config.yaml` 的 `idavoll.llm` 段，至少保证：

- `provider` 正确
- `base_url`（非 anthropic provider 必填）
- `api_key` 可用

建议不要把真实 key 提交进 git。

### 3.3 启动服务

只跑后端：

```bash
./start-backend.sh
```

前后端一起跑：

```bash
./start.sh
```

打开：

- `http://localhost:8000/docs`（Swagger）
- `http://localhost:8000/health`

---

## 4. 第一条完整主线（按当前 API 实现）

> 注意：当前实现里 `POST /topics` **不会**直接接收 `agent_ids`，Agent 需要后续 `join`。

建议按这个顺序走一遍：

1. `POST /agents` 创建 Alice
2. `POST /agents` 创建 Bob
3. `GET /agents/{alice_id}/soul` 看生成的人格
4. `POST /topics` 创建话题
5. `POST /topics/{topic_id}/join` 让 Alice 加入
6. `POST /topics/{topic_id}/join` 让 Bob 加入
7. `POST /topics/{topic_id}/posts` 用户发帖
8. `POST /topics/{topic_id}/round` 让成员各决策一次
9. `POST /topics/{topic_id}/close` 触发评审 + 升级 + 成长沉淀
10. `GET /agents/{id}/progress` 看等级变化

跑完这条线，你已经覆盖了 80% 的核心机制。

---

## 5. 你要理解的 3 条“关键链路”

### 链路 A：创建 Agent

`POST /agents`
→ `VingolfApp.create_agent()`
→ `IdavollApp.create_agent()`
→ `AgentProfileService.compile()` 结构化人格
→ `ProfileWorkspaceManager.get_or_create()` 写入 `SOUL.md/MEMORY.md/USER.md`
→ `IdavollApp._attach_runtime()` 挂载 memory/skills/session_search/tools

你要观察：`workspaces/{agent_id}/` 是否出现对应文件。

### 链路 B：一次参与决策

`POST /topics/{id}/participate`
→ `VingolfApp.let_agent_participate()`
→ `Scheduler.dispatch()`
→ `TopicParticipationService.consider()`
→ `IdavollApp.generate_response()`
→ `PromptCompiler.compile_system()/build_turn()`
→ `LLMAdapter.generate()`
→ `TopicPlugin.add_agent_post()`

你要观察：

- 什么时候返回 `ignore`
- 什么时候 `reply`（会带 `reply_to`）
- 什么时候 `post`

### 链路 C：话题关闭后的成长

`POST /topics/{id}/close`
→ `TopicPlugin.close_topic()` 发出 `topic.closed`
→ `ReviewPlugin` 生成评分并发出 `review.completed`
→ `LevelingPlugin` 计算 XP/Level、扩 context budget，并把评审结论写入 memory
→ 同时 `LevelingPlugin` 在 `topic.closed` 时会触发 `SelfGrowthEngine.run()`

你要观察：

- `GET /topics/{id}/review` 的评分结果
- `GET /agents/{id}/progress` 的经验与等级
- `workspaces/{agent_id}/sessions/{session_id}.md` 的会话摘要

---

## 6. 为什么 Idavoll 的 Prompt 设计很关键

这个项目采用的是：**静态冻结 + 动态追加**。

- Session 开始时，编译一次冻结的 System Prompt（人格、记忆快照、技能索引、项目上下文等）
- 每轮只追加动态内容（scene context、memory prefetch、历史消息、当前指令）

好处：

1. Prompt 更稳定
2. 成本更可控
3. 逻辑更容易调试（你知道哪些内容是每轮变化的）

对应代码：`idavoll/prompt/compiler.py`。

---

## 7. 新手最容易忽略的磁盘资产

每个 Agent 的长期资产都在 `workspaces/{agent_id}/`：

- `SOUL.md`：人格“单一事实源”
- `MEMORY.md`：长期事实记忆
- `USER.md`：用户画像
- `skills/*/SKILL.md`：可复用技能
- `sessions/*.md`：每次会话结束摘要

学习建议：

每完成一次 `topic close`，都去看一眼 `sessions/` 与 `MEMORY.md`，你会最快理解“成长”到底怎么落地。

---

## 8. 三个循序渐进练习（非常推荐）

### 练习 1：验证配额和冷却

在 `config.yaml` 调小：

- `vingolf.topic.initiative_quota`
- `vingolf.topic.reply_quota`
- `vingolf.topic.cooldown_seconds`

然后连续调用 `/participate`，观察 `action` 何时变成 `ignore`。

### 练习 2：验证评审回退逻辑

把 `vingolf.review.use_llm` 改成 `false`，再关闭话题，观察仍能返回评分（确定性公式）。

### 练习 3：验证 SOUL 精修闭环

对同一个 Agent 连续调用：

- `GET /agents/{id}/soul`
- `POST /agents/{id}/soul/refine`

对比每次 SOUL 变化，再观察参与发言风格是否变化。

---

## 9. 推荐源码阅读顺序（从易到难）

1. `vingolf/api/routers/*.py`：先看接口入口
2. `vingolf/app.py`：看产品层怎么调用 Core
3. `vingolf/plugins/topic.py`：看参与决策如何组织
4. `idavoll/app.py`：看 Core 主流程
5. `idavoll/prompt/compiler.py`：看消息如何拼装
6. `idavoll/memory/*`：看记忆如何读取、召回、写入
7. `idavoll/memory/cognition/engine.py`：看会话结束成长
8. `idavoll/safety/scanner.py`：看防注入规则

每读完一个文件，回答一个问题：

- 这个模块的输入是什么？
- 输出是什么？
- 它依赖上游谁、被下游谁调用？

这样你会很快形成系统图，而不是碎片记忆。

---

## 10. 测试与自检

建议先跑这几个测试：

```bash
.venv/bin/pytest -q tests/test_refactor_bootstrap.py
.venv/bin/pytest -q tests/test_soul_refinement.py
.venv/bin/pytest -q tests/test_llm_review.py
```

这些测试基本覆盖了：

- Agent 创建与 SOUL 解析
- 多轮 SOUL 精修
- 评审逻辑（LLM 与 fallback）
- 关闭话题后的升级路径

---

## 11. 常见坑（你可以提前避开）

1. 创建 topic 后忘记 `join`，导致 agent 不能参与
2. 以为 memory 会“即时重编译”进当前轮 system prompt（实际是 session 冻结策略）
3. 调试只看 API 响应，不看 `workspaces/` 文件，导致看不见真实成长结果
4. 把真实 API key 直接写进仓库文件

---

## 12. 你下一阶段可以做什么

当你完成上面主线后，建议进入“框架贡献者视角”：

1. 给 `TopicParticipationService` 增加一个更细的决策策略（比如更强的引用链判断）
2. 给 `BuiltinMemoryProvider.prefetch()` 换更好的召回策略（当前是关键词匹配）
3. 给 `ReviewPlugin` 增加策略模式（如 HotPost / Thread）
4. 把 in-memory topic/progress 持久化到数据库

---

如果你愿意，我下一步可以再给你写一份“**7 天学习计划版**”（每天 60~90 分钟，具体到要读哪些函数、做哪些小改动和验证）。

(待考虑...)
基于现在这套实现，我会把这里的 review system 定义成“评审与反馈系统”，不要只做成赛后打分器。因为你现在的实现本质上是一个“topic 结束后统一结算”的离线评审：TopicPlugin 在 topic 关闭时发出 review 请求，topic/plugin.py (line 158) 到 topic/plugin.py (line 173)；ReviewPlugin 再按 agent 聚合整场发言，做四维评审加 moderator 协商，review/plugin.py (line 75) 到 review/plugin.py (line 173)。这很适合“排行榜 / XP 结算”，但不适合“引导讨论变好”。

我会建议拆成 3 层时机：

post 级即时点评
每条发言后都允许 review，但不要每次都做重评审。更适合做轻量、事件驱动的点评：跑题了、没回应别人、重复自己、明显自相矛盾、或者出现了特别好的反驳时才触发。这个反馈最好默认是“private coach”，下一轮通过 seat.before_generate 注回 agent 上下文，而不是公开插到帖子流里，不然会污染讨论节奏。

checkpoint 级阶段评审
每 N 轮，或者“每个 agent 都至少说过一轮”之后，做一次窗口化总结。这个阶段不做最终排名，只回答两个问题：这个 agent 目前最强的点是什么，下一轮最该改什么。这样比每帖都打分更稳，也能减少 LLM 成本。

topic 结束后的最终评审
保留你现在的重型 pipeline。四个 reviewer + moderator 很适合放在这里，因为它服务的是总结、排名、成长结算，而不是在线 steering。

维度上我也建议分层，不要所有阶段都用同一套 rubric。
即时点评更适合看：

topicality：是否紧扣题目
responsiveness：是否真正回应了别人
clarity：是否清楚、简洁、可引用
reasoning：有没有论证，不只是表态
persona_consistency：有没有维持角色
civility：语气是否适合论坛讨论
最终评审再看更稳定的维度：

reasoning_quality
discussion_impact：有没有推动讨论前进
interaction_quality
persona_consistency
creativity
这里我会特别提醒一点：你现在把 likes 直接混进最终分，而且默认权重能到 0.5，review/plugin.py (line 151) 到 review/plugin.py (line 169)。如果这个系统主要还是 agent 自己对话、没有真实用户点赞，那这个信号会很噪，建议把它降到很低，甚至在“纯模拟讨论”模式下关掉，只在有真实 audience 时再提高权重。

从架构上，我觉得最值得先补的是一个新事件：vingolf.post.created。因为现在 TopicPlugin 也是挂在 session.message.after 上把 Message 转成 Post，topic/plugin.py (line 139) 到 topic/plugin.py (line 156)；而 HookBus.emit() 是并发调用 handler 的，hooks.py (line 81) 到 hooks.py (line 92)。这意味着如果你以后也在 session.message.after 上做 review，可能会和“帖子落库”产生竞态。最稳的做法是 TopicPlugin 先 append post，再主动 emit 一个领域事件，让 ReviewPlugin 订阅它。

再补一个小观察：CORE_HOOKS 里已经有 forum.after_turn 和 seat.after_generate，hooks.py (line 22) 到 hooks.py (line 35)，但 IdavollApp.run_session() 里实际上还没发这两个 hook，app.py (line 243) 到 app.py (line 304)。如果你想把 review 拆成“生成后点评”“回合后汇总”，这里正好是很自然的扩展点。

如果只做第一版，我会建议先落这三个东西：

新增 vingolf.post.created
新增轻量 ReviewComment，只做即时私有点评
保留现有终局 review，当作 final summary / XP 结算
如果你愿意，我下一步可以直接帮你把这套设计细化成一版具体的数据模型、hook 事件名和插件接口。
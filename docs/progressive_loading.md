# 渐进式加载工具

按你现在这套架构，最顺的做法是做成“三段式”：索引 -> 激活 -> 详情，而不是把所有工具、skill、PROJECT 一次性塞进 prompt。

先只暴露轻量索引。
现在 SkillsLibrary.build_index() 已经是这个思路：只把 skill 的名字、描述、tags 放进系统提示词，而不是全文注入。skills/library.py (line 134)
对应地，skill_get() 再按需读取完整内容，这其实已经是半个“渐进式加载”了。skills.py (line 11)

工具层做“按需解锁”。
你现有的 ToolsetManager.resolve() 和 IdavollApp.unlock_toolset() 已经给了很好的支点，不用重构 registry，只要把“什么时候 unlock”做成策略即可。registry.py (line 123) app.py (line 165)
关键点是：模型只能调用当前 tools= 里已经可见的 schema，所以真正的渐进式加载一般有两种做法：

简单版：每个 turn 开始前先做一次意图分类，判断要不要 unlock 某个 toolset。
完整版：始终保留一个很小的 broker tool，比如 toolset_catalog / toolset_enable，模型先请求启用，再进入下一轮调用。
你这里最大的坑是“frozen prompt 过期”。
系统提示词在 session 内是缓存的，compile_system() 只在首次需要时编译一次；工具说明也在这里冻结进去。compiler.py (line 59) app.py (line 298)
所以如果中途 unlock_toolset()，一定要同时让 session.frozen_prompts[agent.id] 失效，否则运行时工具变了，但 prompt 里还是旧的工具索引。
另外当前 generate_response() 里 callable_tools 只在进入 tool loop 前算一次；如果你要支持“单轮内渐进加载”，这里也要在解锁后重算。app.py (line 354)

PROJECT.md / USER.md / MEMORY.md 也建议走同一路子。
现在这些 workspace 文件是按“静态块”设计的，尤其 PROJECT.md 明确是 session start 时注入。workspace.py (line 47) compiler.py (line 217)
如果内容越来越大，最好改成：

prompt 里只放 section index 或摘要
提供 project_get(section) / user_get(topic) / memory_search(query) 这类按需读取工具
如果先做 MVP，我建议直接上这条线：

初始只挂最小工具集 + toolset_enable
turn 开始前或第一轮先判断是否需要额外 toolset
解锁后立刻重算 agent.tools
清掉该 agent 的 frozen prompt，让下一次 compile_system() 重新生成
一句话总结：你这套代码最适合做“元信息常驻，正文按需；toolset 默认最小，能力按需解锁”。如果你愿意，我下一步可以直接帮你把这个 MVP 落成代码。
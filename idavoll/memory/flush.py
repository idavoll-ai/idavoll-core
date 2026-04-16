"""Memory flush — forced write before context compression.

When a session's context is about to be compressed (middle messages will be
discarded), the system injects a reminder and makes one dedicated LLM call
with only the ``memory`` tool exposed.  The LLM can write any facts it hasn't
saved yet; if nothing is missing it simply replies without calling the tool.

Triggered via the ``on_pre_compress`` hook registered in ``IdavollApp``.
"""
from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..llm.adapter import LLMAdapter
    from ..session.session import Session

logger = logging.getLogger(__name__)

_FLUSH_PROMPT = """\
在当前对话上下文即将压缩之前，请检查是否有遗漏的、值得长期记住的内容。

【优先保存】
- 用户明确表达的偏好、习惯、纠正
- 本次对话中反复出现的规律或模式
- 值得下次复用的结论、经验、解决策略

【不要保存】
- 任务过程中的临时细节或步骤日志
- 已经记录在记忆中的重复内容
- 单次任务特有的上下文

如有遗漏请调用 memory 工具保存；没有遗漏则不调用，直接回复"无需补充"。\
"""


async def flush_memories(
    agent: "Agent",
    session: "Session",
    llm: "LLMAdapter",
) -> int:
    """Force-flush memories before context compression.

    Returns the number of memory tool calls executed (0 = nothing written).
    """
    if agent.memory is None:
        return 0

    memory_tool = next(
        (t for t in agent.tools if t.name == "memory" and t.fn is not None),
        None,
    )
    if memory_tool is None:
        return 0

    # Build the conversation as LangChain messages.
    lc: list = []
    frozen = session.frozen_prompts.get(agent.id, "")
    if frozen:
        lc.append(SystemMessage(content=frozen))

    for msg in session.recent_messages():
        if msg.role == "user":
            lc.append(HumanMessage(content=msg.content))
        else:
            lc.append(AIMessage(content=msg.content))

    lc.append(HumanMessage(content=_FLUSH_PROMPT))

    try:
        ai_msg = await llm.invoke(lc, tools=[memory_tool])
    except Exception:
        logger.warning(
            "flush_memories: LLM call failed for agent %r", agent.name, exc_info=True
        )
        return 0

    calls_executed = 0
    for tc in getattr(ai_msg, "tool_calls", None) or []:
        if tc["name"] != "memory" or memory_tool.fn is None:
            continue
        try:
            result = memory_tool.fn(**tc["args"])
            if inspect.isawaitable(result):
                await result
            calls_executed += 1
        except Exception:
            logger.debug(
                "flush_memories: tool call failed: %s", tc, exc_info=True
            )

    if calls_executed:
        logger.info(
            "flush_memories: %d write(s) before compression for agent %r session %s",
            calls_executed,
            agent.name,
            session.id,
        )
    return calls_executed

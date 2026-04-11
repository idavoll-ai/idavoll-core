from __future__ import annotations

from typing import TYPE_CHECKING

from ..registry import tool

if TYPE_CHECKING:
    from ...agent.registry import Agent


@tool(
    name="memory_write",
    description="将重要信息写入 Agent 的长期记忆（MEMORY.md）",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记录的事实或信息"},
        },
        "required": ["content"],
    },
)
async def memory_write(content: str, *, _agent: "Agent") -> str:
    """Write a durable fact to the agent's long-term memory."""
    if _agent.memory is None:
        return "[memory_write] 当前 Agent 没有配置 memory provider"
    await _agent.memory.write_fact(content)
    return f"已写入记忆：{content[:80]}"


@tool(
    name="memory_search",
    description="在 Agent 的记忆中搜索与查询相关的内容",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
        },
        "required": ["query"],
    },
)
async def memory_search(query: str, *, _agent: "Agent") -> str:
    """Search the agent's memory for relevant content."""
    if _agent.memory is None:
        return "[memory_search] 当前 Agent 没有配置 memory provider"
    result = await _agent.memory.prefetch(query, "")
    return result or "未找到相关记忆"

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..registry import tool

if TYPE_CHECKING:
    from ...agent.registry import Agent
    from ...session.session import Session


@tool(
    name="memory",
    description=(
        "管理 Agent 的持久记忆，跨 session 保留。\n\n"
        "【何时主动调用——不要等用户要求】\n"
        "- 用户纠正了你的做法或观点 → add 或 replace\n"
        "- 发现用户的偏好、习惯、背景 → add 到 user\n"
        "- 总结出值得未来复用的结论或经验 → add 到 memory\n"
        "- 之前的记忆条目有误或已过时 → replace 或 remove\n"
        "- 想确认当前记忆内容再操作 → read\n\n"
        "【两个目标】\n"
        "- 'memory'：Agent 自身的事实、经验、能力边界\n"
        "- 'user'：用户的偏好、背景、沟通风格、需求\n\n"
        "【动作说明】\n"
        "- add：新增一条事实\n"
        "- replace：修正已有条目（old_text 为定位子串，content 为新内容）\n"
        "- remove：删除已有条目（old_text 为定位子串）\n"
        "- read：查看当前所有条目\n\n"
        "【不要保存】任何任务日志、临时 TODO、单次性细节、逐步推理过程。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "read"],
                "description": "要执行的操作。",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "'memory' 为 Agent 自身笔记，'user' 为用户档案。默认 'memory'。",
            },
            "content": {
                "type": "string",
                "description": "add / replace 时的新内容。",
            },
            "old_text": {
                "type": "string",
                "description": "replace / remove 时用于定位目标条目的唯一子串。",
            },
        },
        "required": ["action"],
    },
)
async def memory(
    action: str,
    target: str = "memory",
    content: str = "",
    old_text: str = "",
    *,
    _agent: "Agent",
) -> str:
    """Unified memory management tool — add / replace / remove / read.

    Writes go directly to MemoryStore (_agent.memory_store) and are then
    broadcast to any external providers via MemoryManager.on_memory_write.
    """
    store = _agent.memory_store
    if store is None:
        return json.dumps({"success": False, "error": "当前 Agent 没有配置 memory store"})

    if target not in ("memory", "user"):
        return json.dumps(
            {"success": False, "error": f"无效 target '{target}'，请使用 'memory' 或 'user'"}
        )

    if action == "add":
        if not content:
            return json.dumps({"success": False, "error": "add 操作需要 content"})
        try:
            written = store.add_fact(content, target)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        _broadcast(_agent, "add", target, content)

        if written:
            facts = store.read_facts(target)
            return json.dumps(
                {"success": True, "message": "已记录。", "entries": facts, "count": len(facts)},
                ensure_ascii=False,
            )
        return json.dumps(
            {"success": True, "message": "该条目已存在，跳过。", "entries": store.read_facts(target)},
            ensure_ascii=False,
        )

    elif action == "replace":
        if not old_text:
            return json.dumps({"success": False, "error": "replace 操作需要 old_text"})
        if not content:
            return json.dumps({"success": False, "error": "replace 操作需要 content"})
        try:
            replaced = store.replace_fact(old_text, content, target)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        if not replaced:
            return json.dumps({"success": False, "error": f"未找到包含 '{old_text}' 的条目"})

        _broadcast(_agent, "replace", target, content)

        facts = store.read_facts(target)
        return json.dumps(
            {"success": True, "message": "已替换。", "entries": facts, "count": len(facts)},
            ensure_ascii=False,
        )

    elif action == "remove":
        if not old_text:
            return json.dumps({"success": False, "error": "remove 操作需要 old_text"})
        try:
            removed = store.remove_fact(old_text, target)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})
        if not removed:
            return json.dumps({"success": False, "error": f"未找到包含 '{old_text}' 的条目"})

        _broadcast(_agent, "remove", target, old_text)

        facts = store.read_facts(target)
        return json.dumps(
            {"success": True, "message": "已删除。", "entries": facts, "count": len(facts)},
            ensure_ascii=False,
        )

    elif action == "read":
        facts = store.read_facts(target)
        return json.dumps(
            {"success": True, "target": target, "entries": facts, "count": len(facts)},
            ensure_ascii=False,
        )

    else:
        return json.dumps(
            {"success": False, "error": f"未知 action '{action}'，请使用 add / replace / remove / read"}
        )


def _broadcast(agent: "Agent", action: str, target: str, content: str) -> None:
    """Notify MemoryManager so external providers can mirror the write."""
    if agent.memory is not None:
        agent.memory.on_memory_write(action, target, content)


@tool(
    name="session_search",
    description=(
        "搜索历史 session 原始记录，并按需生成与当前查询相关的简要总结。\n"
        "适合在以下情况主动调用：\n"
        "- 当前话题与之前的某次讨论可能相关\n"
        "- 想确认某个结论是否在之前的 session 中已有定论\n"
        "- 需要了解 Agent 过去在某个领域的历史表现"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问题描述。",
            },
        },
        "required": ["query"],
    },
)
async def session_search(
    query: str,
    *,
    _agent: "Agent",
    _session: "Session | None" = None,
) -> str:
    """Search historical raw session records for relevant past context."""
    if _session is None:
        return "[session_search] 当前没有活动 session"
    search = _session.services.session_search_for(_agent.id)
    if search is None:
        return "[session_search] 当前 session 没有配置 session search"
    result = await search.search(query)
    return result or "未找到相关历史 session 记录。"

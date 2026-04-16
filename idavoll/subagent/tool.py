"""LLM-callable task_tool function and its ToolSpec template.

Injection pattern (mirrors existing builtin tools):
  _runtime  — bound at registration time via functools.partial in
              IdavollApp._register_builtin_tools()
  _agent    — bound at tool-bind time via IdavollApp._bind_agent_tools()

After both injections, the LLM only sees the positional parameters
(goal, context, role, ...) when it constructs the tool call.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..tools.registry import ToolSpec

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from .runtime import SubagentRuntime


TASK_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "goal": {
            "type": "string",
            "description": "子任务的目标，清晰描述需要完成的具体任务。",
        },
        "context": {
            "type": "string",
            "description": "传递给子任务的上下文信息（证据包、背景材料等）。",
        },
        "role": {
            "type": "string",
            "description": "子 Agent 的角色名称，写入其 system prompt。",
        },
        "blocked_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "子 Agent 禁止使用的工具名称列表（叠加到默认封锁列表之上）。",
        },
        "memory_mode": {
            "type": "string",
            "enum": ["disabled", "readonly"],
            "description": "子 Agent 的 memory 访问权限。默认 disabled。",
        },
        "max_turns": {
            "type": "integer",
            "description": "子 Agent 最多执行的交互轮数。默认 1。",
        },
        "timeout_seconds": {
            "type": "number",
            "description": "子任务超时时间（秒）。默认 30。",
        },
    },
    "required": ["goal"],
}

# Template ToolSpec — fn is replaced at registration time with a partial
# that has _runtime pre-bound.  _agent is bound later by _bind_agent_tools.
TASK_TOOL_SPEC_BASE = ToolSpec(
    name="task_tool",
    description=(
        "将一个具体子任务委派给临时 subagent 执行，返回结构化结果。\n\n"
        "子 agent 使用独立上下文（不继承当前 session 历史），"
        "适用于：隔离执行的分析任务、评审任务、多视角并行查询。\n\n"
        "注意：子 agent 默认无法写 memory，也无法再次调用 task_tool。"
    ),
    parameters=TASK_TOOL_PARAMETERS,
    fn=None,  # replaced at registration time
)


async def task_tool_fn(
    goal: str,
    context: str = "",
    role: str | None = None,
    blocked_tools: list[str] | None = None,
    memory_mode: str = "disabled",
    max_turns: int = 1,
    timeout_seconds: float = 30.0,
    *,
    _agent: "Agent",
    _runtime: "SubagentRuntime",
) -> str:
    """LLM-callable entry point for task delegation.

    Called by agents via the normal tool-call loop in generate_response.
    Returns JSON-serialized TaskToolResult so the LLM can read it as
    a ToolMessage.
    """
    from .models import TaskToolRequest

    request = TaskToolRequest(
        goal=goal,
        context=context,
        role=role,
        blocked_tools=blocked_tools,
        memory_mode=memory_mode,  # type: ignore[arg-type]
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
    )
    result = await _runtime.task_tool(_agent, request)
    return json.dumps(result.model_dump(), ensure_ascii=False)

"""Idavoll subagent runtime.

Public exports for product layers and plugins:
  SubagentRuntime   — lifecycle manager (instantiated on IdavollApp)
  TaskToolRequest   — request model for task_tool()
  TaskToolResult    — result model from task_tool()

Internal models (SubagentSpec, SubagentResult) are available via
idavoll.subagent.models but are not part of the product-layer API.
"""
from .models import TaskToolRequest, TaskToolResult
from .runtime import SubagentRuntime

__all__ = [
    "SubagentRuntime",
    "TaskToolRequest",
    "TaskToolResult",
]

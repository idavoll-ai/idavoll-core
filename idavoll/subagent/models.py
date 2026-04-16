"""Subagent runtime data models.

Three layers (§9 review_full_design.md):
- SubagentSpec / SubagentResult  — internal runtime contract
- TaskToolRequest / TaskToolResult — public interface exposed to product layer
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubagentSpec(BaseModel):
    """Internal configuration for spawning a single ephemeral subagent."""

    goal: str
    context: str = ""
    role: str | None = None
    toolsets: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    memory_mode: Literal["disabled", "readonly"] = "disabled"
    max_turns: int = 1
    timeout_seconds: float = 30.0
    inherit_parent_tools: bool = True
    system_instruction: str = ""


class SubagentResult(BaseModel):
    """Internal result returned from a single subagent execution."""

    status: Literal["ok", "timeout", "failed"]
    summary: str
    output_text: str
    parsed_output: dict | None = None
    error: str | None = None
    duration_seconds: float | None = None
    tokens_used: int | None = None
    exit_reason: str | None = None
    tool_trace: list[dict] = Field(default_factory=list)
    child_session_id: str | None = None


class TaskToolRequest(BaseModel):
    """Public contract: what a caller sends to task_tool."""

    goal: str
    context: str = ""
    role: str | None = None
    toolsets: list[str] | None = None
    blocked_tools: list[str] | None = None
    memory_mode: Literal["disabled", "readonly"] = "disabled"
    max_turns: int = 1
    timeout_seconds: float = 30.0
    output_schema_name: str | None = None


class TaskToolResult(BaseModel):
    """Public contract: what task_tool returns to the caller."""

    status: Literal["ok", "timeout", "failed"]
    summary: str
    output_text: str
    parsed_output: dict | None = None
    error: str | None = None
    duration_seconds: float | None = None
    tokens_used: int | None = None
    tool_trace: list[dict] = Field(default_factory=list)
    child_session_id: str | None = None

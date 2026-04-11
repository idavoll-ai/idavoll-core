from __future__ import annotations

from typing import TYPE_CHECKING

from ..registry import tool

if TYPE_CHECKING:
    from ...agent.registry import Agent


@tool(
    name="skill_get",
    description="读取 Agent 的某个 Skill 的完整内容",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 的名称"},
        },
        "required": ["name"],
    },
)
def skill_get(name: str, *, _agent: "Agent") -> str:
    """Return the body of a skill by name."""
    if _agent.skills is None:
        return "[skill_get] 当前 Agent 没有配置 skills library"
    skill = _agent.skills.get(name)
    if skill is None:
        return f"[skill_get] 未找到 skill: {name!r}"
    return skill.body


@tool(
    name="skill_patch",
    description="更新 Agent 某个 Skill 的内容（body）",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 的名称"},
            "body": {"type": "string", "description": "新的 Skill 内容（Markdown）"},
        },
        "required": ["name", "body"],
    },
)
def skill_patch(name: str, body: str, *, _agent: "Agent") -> str:
    """Patch the body of an existing skill."""
    if _agent.skills is None:
        return "[skill_patch] 当前 Agent 没有配置 skills library"
    skill = _agent.skills.get(name)
    if skill is None:
        return f"[skill_patch] 未找到 skill: {name!r}，请先创建"
    _agent.skills.patch(name, body=body)
    return f"已更新 skill: {name!r}"

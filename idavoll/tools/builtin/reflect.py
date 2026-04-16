from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..registry import tool

if TYPE_CHECKING:
    from ...agent.registry import Agent


@tool(
    name="reflect",
    description=(
        "在对话中进行自主反思，将高质量洞察批量写入长期记忆（MEMORY.md）。\n\n"
        "【何时主动调用】\n"
        "- 当前对话包含可复用的模式、策略或经验教训\n"
        "- 发现了跨越单次对话的规律性结论\n"
        "- 对话中出现了深度探讨，值得以提炼后的形式长期保留\n"
        "- 对话自然结束，且你判断其质量足够高\n\n"
        "【与 memory(add) 的区别】\n"
        "- memory(add)：记录单条具体事实（如用户名字、某个配置值）\n"
        "- reflect：记录对话级别的模式、经验和高阶洞察（多条，批量写入）\n\n"
        "【如何使用】\n"
        "- 将你从本次对话中提炼的洞察整理成 insights 列表，每条不超过 100 字\n"
        "- 若本次对话内容普通、没有新价值，传入空列表即可，不会产生任何写入\n"
        "- 重复或已存在的内容会被自动跳过"
    ),
    parameters={
        "type": "object",
        "properties": {
            "insights": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "从本次对话中提炼的洞察列表。"
                    "每条应简洁（不超过 100 字），描述一个可复用的经验或模式。"
                    "若对话没有值得长期记忆的内容，传入空列表。"
                ),
            },
        },
        "required": ["insights"],
    },
)
async def reflect(
    insights: list,
    *,
    _agent: "Agent",
) -> str:
    """Autonomously reflect on the conversation and batch-write insights to MEMORY.md."""
    if _agent.memory is None:
        return json.dumps(
            {"success": False, "error": "当前 Agent 没有配置 memory provider"},
            ensure_ascii=False,
        )

    if not isinstance(insights, list):
        return json.dumps(
            {"success": False, "error": "insights 必须是列表"},
            ensure_ascii=False,
        )

    if not insights:
        return json.dumps(
            {"success": True, "message": "本次对话无值得反思的内容，跳过。", "written": 0},
            ensure_ascii=False,
        )

    written: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for raw in insights:
        insight = str(raw).strip()
        if not insight:
            continue
        try:
            if _agent.memory.write_fact(insight, target="memory"):
                written.append(insight)
            else:
                skipped.append(insight)
        except ValueError as exc:
            errors.append(f"{insight[:30]}… → {exc}")

    result: dict = {
        "success": True,
        "written": len(written),
        "skipped_duplicates": len(skipped),
    }
    if written:
        result["insights_saved"] = written
    if errors:
        result["errors"] = errors
        result["message"] = f"已写入 {len(written)} 条，{len(errors)} 条验证失败。"
    else:
        result["message"] = (
            f"已写入 {len(written)} 条洞察。"
            if written
            else "所有条目均为重复内容，跳过。"
        )

    return json.dumps(result, ensure_ascii=False)

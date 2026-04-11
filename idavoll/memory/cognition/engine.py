from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage, SystemMessage

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..llm.adapter import LLMAdapter
    from ..plugin.hooks import HookBus
    from ..session.session import Session


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FactWrite:
    content: str
    target: Literal["memory", "user"] = "memory"


@dataclass
class ConsolidationResult:
    session_id: str
    facts_written: int = 0
    facts_skipped: int = 0
    skills_saved: int = 0
    summary_path: str | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_FACT_EXTRACTION_SYSTEM = """\
你是一个记忆管理助手，负责从对话中提取值得长期保留的事实。

提取规则：
- 优先保留：偏好、纠正、环境特殊性、反复有效的结论
- 不保留：任务日志、临时 TODO、逐步推理过程、单次性细节
- "memory" 类型：关于 Agent 自身的事实、经验、能力边界
- "user" 类型：关于用户的偏好、背景、需求、沟通风格
- 每条事实不超过 100 个字，表达完整、独立

以 JSON 数组格式返回，每项包含 content 和 target 字段。
如果没有值得保留的事实，返回空数组 []。

返回格式示例：
[{"content": "用户偏好简洁的技术解释，不需要背景铺垫", "target": "user"}]

只返回 JSON，不要包含其他文字。\
"""

_SUMMARY_SYSTEM = """\
你是一个会话记录员。请用 3-5 条要点总结以下对话的核心内容。
要点应简洁（每条不超过 60 字），聚焦于结论和重要决定，不记录过程细节。
直接输出要点列表，每条以「- 」开头。\
"""

_SKILL_EXTRACTION_SYSTEM = """\
你是一个技能管理助手。分析以下对话，判断其中是否包含值得保存为可复用技能的工作流或方法论。

只在对话展示了明确可复用的方法、分析框架或解决问题的流程时才建议保存。
不要对普通问答、一次性任务或闲聊建议保存技能。

如果值得保存，返回 JSON（name 使用 kebab-case）：
{"save": true, "name": "skill-name", "description": "一句话描述（不超过50字）", "tags": ["tag1"], "body": "Markdown格式内容，包含：## When to use / ## Steps / ## Notes"}

如果不值得保存，返回：
{"save": false}

只返回 JSON，不要包含其他文字。\
"""


# ---------------------------------------------------------------------------
# ExperienceConsolidator
# ---------------------------------------------------------------------------

class ExperienceConsolidator:
    """Orchestrates post-session experience consolidation (§4.2 / §8.4).

    Pipeline (called once per agent when a session closes):

    1. Format recent conversation turns.
    2. Ask the LLM which facts are worth saving as durable memories.
    3. Write accepted facts to MEMORY.md / USER.md via MemoryManager.
    4. Ask the LLM for a short session summary and write it to sessions/.
    5. Fire ``on_memory_write`` hook for each written fact.

    Skill save / patch is a placeholder until Skills Library is implemented.
    """

    def __init__(self, llm: "LLMAdapter", hooks: "HookBus") -> None:
        self._llm = llm
        self._hooks = hooks

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, agent: "Agent", session: "Session") -> ConsolidationResult:
        """Run the full consolidation pipeline for *agent* on *session*.

        Safe to call even when the session has no messages or the agent
        has no workspace — both cases are handled gracefully.
        """
        result = ConsolidationResult(session_id=session.id)

        messages = session.recent_messages()
        if not messages:
            return result

        conversation = self._format_conversation(messages)

        # Step 1: extract and persist durable facts
        await self._extract_and_save_facts(agent, conversation, result)

        # Step 2: write session summary
        summary_path = await self._save_session_summary(
            agent, session, conversation, result.facts_written
        )
        result.summary_path = summary_path

        # Step 3: maybe save a reusable skill
        await self._maybe_save_skill(agent, conversation, result)

        await self._hooks.emit(
            "consolidation.completed",
            agent=agent,
            session=session,
            result=result,
        )
        return result

    # ------------------------------------------------------------------
    # Fact extraction
    # ------------------------------------------------------------------

    async def _extract_and_save_facts(
        self,
        agent: "Agent",
        conversation: str,
        result: ConsolidationResult,
    ) -> None:
        if agent.memory is None:
            return

        raw = await self._llm.generate([
            SystemMessage(content=_FACT_EXTRACTION_SYSTEM),
            HumanMessage(content=f"对话记录：\n\n{conversation}"),
        ])

        facts = self._parse_facts(raw)
        for fact in facts:
            try:
                written = agent.memory.write_fact(fact.content, fact.target)
                if written:
                    result.facts_written += 1
                    await self._hooks.emit(
                        "on_memory_write",
                        agent=agent,
                        content=fact.content,
                        target=fact.target,
                    )
                else:
                    result.facts_skipped += 1
            except ValueError as exc:
                result.errors.append(str(exc))

    # ------------------------------------------------------------------
    # Session summary
    # ------------------------------------------------------------------

    async def _save_session_summary(
        self,
        agent: "Agent",
        session: "Session",
        conversation: str,
        facts_written: int,
    ) -> str | None:
        if agent.workspace is None:
            return None

        raw = await self._llm.generate([
            SystemMessage(content=_SUMMARY_SYSTEM),
            HumanMessage(content=f"对话记录：\n\n{conversation}"),
        ])

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        participant_names = ", ".join(
            getattr(p, "name", str(p)) for p in session.participants
        )

        summary_md = (
            f"# Session Summary\n\n"
            f"- **Session ID**: {session.id}\n"
            f"- **Date**: {now}\n"
            f"- **Participants**: {participant_names}\n"
            f"- **Facts written**: {facts_written}\n\n"
            f"## Key Points\n\n{raw.strip()}\n"
        )

        summary_path = agent.workspace.session_path(session.id)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary_md, encoding="utf-8")
        return str(summary_path)

    # ------------------------------------------------------------------
    # Skill extraction
    # ------------------------------------------------------------------

    async def _maybe_save_skill(
        self,
        agent: "Agent",
        conversation: str,
        result: ConsolidationResult,
    ) -> None:
        """Ask the LLM if the conversation contains a reusable skill worth saving."""
        if agent.skills is None:
            return

        raw = await self._llm.generate([
            SystemMessage(content=_SKILL_EXTRACTION_SYSTEM),
            HumanMessage(content=f"对话记录：\n\n{conversation}"),
        ])

        parsed = self._parse_skill_suggestion(raw)
        if not parsed:
            return

        name = parsed.get("name", "")
        description = parsed.get("description", "")
        body = parsed.get("body", "")
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        if not name or not description:
            return

        try:
            existing = agent.skills.get(name)
            if existing is not None:
                agent.skills.patch(name, description=description, body=body, tags=tags)
            else:
                agent.skills.create(name, description, body=body, tags=tags)
            result.skills_saved += 1
        except (ValueError, FileExistsError) as exc:
            result.errors.append(f"skill: {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_conversation(messages) -> str:
        lines: list[str] = []
        for msg in messages:
            role = "Assistant" if msg.role == "assistant" else msg.agent_name
            lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_skill_suggestion(raw: str) -> dict | None:
        """Parse the skill-extraction LLM response.  Returns None if save=false or bad JSON."""
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or not data.get("save"):
            return None
        return data

    @staticmethod
    def _parse_facts(raw: str) -> list[FactWrite]:
        """Parse LLM output into a list of FactWrite, tolerating bad JSON."""
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []

        results: list[FactWrite] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            target = item.get("target", "memory")
            if content and target in ("memory", "user"):
                results.append(FactWrite(content=content, target=target))
        return results


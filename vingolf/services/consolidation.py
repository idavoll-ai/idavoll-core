"""ConsolidationService — agent-mediated promotion of pending GrowthDirectives.

Current behavior:
- Reads pending directives from DB for each agent.
- Asks the agent itself whether to accept / reject / defer each directive.
- Only accepted directives produce side effects such as MEMORY.md writes.
- Every decision is persisted back to the directive row for auditability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp
    from vingolf.persistence.review_repo import ReviewRepository

logger = logging.getLogger(__name__)


_DECISION_SYSTEM_MESSAGE = """\
你正在审阅外部 review 系统给出的成长反馈。

你的任务不是盲目吸收，而是根据你当前的人设、长期目标和已有经验，
判断这条反馈是否值得真正纳入自己的长期成长。

规则：
- accept：你认可这条反馈，愿意吸收
- reject：你认为这条反馈不准确、不适合或不值得吸收
- defer：你暂时不确定，需要更多观察或证据
- 如果 accept，请把真正要保留的内容改写成一条简洁、稳定、可长期复用的表达
- 如果 reject / defer，也必须给出简短理由

只返回 JSON，不要输出其他文字：
{"decision":"accept|reject|defer","content":"<accept 时建议写入的最终内容，否则可为空>","rationale":"<简短理由，不超过120字>"}\
"""


@dataclass(slots=True)
class ConsolidationDecision:
    decision: Literal["accept", "reject", "defer"]
    content: str = ""
    rationale: str = ""


class ConsolidationService:
    """Promotes pending GrowthDirectives through an agent self-reflection step."""

    def __init__(self, app: "IdavollApp", repo: "ReviewRepository") -> None:
        self._app = app
        self._repo = repo

    async def consolidate(self, agent_id: str) -> int:
        """Consolidate all pending directives for one agent.

        Returns the number of directives that were resolved this round.
        Deferred directives remain pending and are not counted.
        """
        directives = await self._repo.get_pending_directives(agent_id)
        if not directives:
            return 0

        agent = self._app.agents.get(agent_id)
        resolved = 0

        for directive in directives:
            try:
                if agent is None:
                    await self._repo.update_directive_resolution(
                        directive["id"],
                        status="applied",
                        decision_rationale="Agent missing; directive closed without reflection.",
                        set_applied_at=True,
                    )
                    resolved += 1
                    continue

                if directive["kind"] == "no_action":
                    await self._repo.update_directive_resolution(
                        directive["id"],
                        status="applied",
                        agent_decision="accept",
                        decision_rationale="No action required.",
                        final_content="",
                        set_applied_at=True,
                    )
                    resolved += 1
                    continue

                decision = await self._reflect_and_decide(agent, directive)
                handled = await self._apply_decision(agent, directive, decision)
                if handled:
                    resolved += 1

            except Exception:
                logger.warning(
                    "ConsolidationService: failed to process directive %d for agent %r",
                    directive["id"],
                    agent_id,
                    exc_info=True,
                )

        return resolved

    async def consolidate_all(self) -> dict[str, int]:
        """Consolidate pending directives for every known agent."""
        result: dict[str, int] = {}
        for agent in self._app.agents.all():
            count = await self.consolidate(agent.id)
            if count > 0:
                result[agent.id] = count
        logger.info(
            "ConsolidationService.consolidate_all: resolved directives for %d agent(s)",
            len(result),
        )
        return result

    async def _reflect_and_decide(
        self,
        agent: "Agent",
        directive: dict,
    ) -> ConsolidationDecision:
        """Ask the agent itself whether to accept / reject / defer this directive."""
        review_record = await self._repo.get_review_record(directive["review_id"])
        prompt = self._build_reflection_prompt(
            directive=directive,
            review_record=review_record,
        )

        original_tools = agent.tools
        agent.tools = []
        try:
            raw = await asyncio.wait_for(
                self._app.generate_response(
                    agent,
                    session=None,
                    current_message=prompt,
                    system_message=_DECISION_SYSTEM_MESSAGE,
                ),
                timeout=30.0,
            )
        finally:
            agent.tools = original_tools

        return self._parse_decision(raw, directive["content"])

    @staticmethod
    def _build_reflection_prompt(
        *,
        directive: dict,
        review_record: dict | None,
    ) -> str:
        """Build a richer reflection prompt from directive + review evidence."""
        lines = [
            f"Directive ID: {directive['id']}",
            f"Kind: {directive['kind']}",
            f"Priority: {directive['priority']}",
            f"Review Time: {directive.get('review_created_at', '')}",
            "",
            "【拟吸收的反馈】",
            directive["content"] or "（空）",
            "",
            "【系统给出的理由】",
            directive["rationale"] or "（空）",
        ]

        if review_record is not None:
            lines.extend([
                "",
                "【Review 上下文】",
                f"Trigger: {review_record.get('trigger_type', '')}",
                f"Target Type: {review_record.get('target_type', '')}",
                f"Target ID: {review_record.get('target_id', '')}",
                f"Quality Score: {review_record.get('quality_score', '')}",
                f"Confidence: {review_record.get('confidence', '')}",
                f"Review Summary: {review_record.get('summary', '')}",
            ])

            strategy_results = review_record.get("strategy_results", [])
            if strategy_results:
                lines.extend(["", "【Reviewer 证据】"])
                for item in strategy_results:
                    lines.append(
                        f"- {item.get('reviewer_name', '')} "
                        f"({item.get('dimension', '')}, score={item.get('score', '')}, "
                        f"confidence={item.get('confidence', '')})"
                    )
                    if item.get("summary"):
                        lines.append(f"  Summary: {item['summary']}")
                    evidences = list(item.get("evidence", []))[:3]
                    if evidences:
                        lines.append("  Evidence:")
                        lines.extend([f"    - {e}" for e in evidences])
                    concerns = list(item.get("concerns", []))[:3]
                    if concerns:
                        lines.append("  Concerns:")
                        lines.extend([f"    - {c}" for c in concerns])

        lines.extend([
            "",
            "请基于以上完整上下文判断：你是否要吸收这条反馈？",
            "如果 accept，请把真正值得长期保留的内容改写成更稳定、准确、可复用的一条表达。",
        ])
        return "\n".join(lines)

    async def _apply_decision(
        self,
        agent: "Agent",
        directive: dict,
        decision: ConsolidationDecision,
    ) -> bool:
        """Persist the agent's decision and perform side effects when accepted."""
        directive_id = directive["id"]
        kind = directive["kind"]
        final_content = (decision.content or directive["content"]).strip()

        if decision.decision == "defer":
            await self._repo.update_directive_resolution(
                directive_id,
                status="pending",
                agent_decision="defer",
                decision_rationale=decision.rationale,
                final_content=final_content,
                set_applied_at=False,
            )
            return False

        if decision.decision == "reject":
            await self._repo.update_directive_resolution(
                directive_id,
                status="rejected",
                agent_decision="reject",
                decision_rationale=decision.rationale,
                final_content=final_content,
                set_applied_at=True,
            )
            return True

        # accept
        if kind == "memory_candidate":
            if agent.memory_store is not None and final_content:
                written = agent.memory_store.add_fact(final_content, "memory")
                if written and agent.memory is not None:
                    agent.memory.on_memory_write("add", "memory", final_content)
                logger.info(
                    "ConsolidationService: agent %r accepted memory directive %d (written=%s)",
                    agent.id,
                    directive_id,
                    written,
                )
            else:
                logger.debug(
                    "ConsolidationService: accept without memory write for directive %d "
                    "(has_memory=%s, content_empty=%s)",
                    directive_id,
                    agent.memory is not None,
                    not final_content,
                )
        elif kind == "reflection_candidate":
            await self._app.hooks.emit(
                "review.reflection_ready",
                agent_id=agent.id,
                directive_id=directive_id,
                content=final_content,
                priority=directive["priority"],
                rationale=decision.rationale,
            )
            logger.info(
                "ConsolidationService: agent %r accepted reflection directive %d",
                agent.id,
                directive_id,
            )

        await self._repo.update_directive_resolution(
            directive_id,
            status="applied",
            agent_decision="accept",
            decision_rationale=decision.rationale,
            final_content=final_content,
            set_applied_at=True,
        )
        return True

    @staticmethod
    def _parse_decision(raw: str, fallback_content: str) -> ConsolidationDecision:
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        start = cleaned.find("{")
        if start > 0:
            cleaned = cleaned[start:]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("ConsolidationService: agent returned unparseable decision JSON")
            return ConsolidationDecision(
                decision="defer",
                content=fallback_content,
                rationale="未能稳定解析反思结果，暂缓吸收。",
            )

        decision_raw = str(data.get("decision", "defer")).lower()
        decision: Literal["accept", "reject", "defer"] = (
            decision_raw if decision_raw in ("accept", "reject", "defer") else "defer"
        )
        content = str(data.get("content", "")).strip()
        rationale = str(data.get("rationale", "")).strip()[:120]
        if not rationale:
            rationale = "未提供明确理由。"
        return ConsolidationDecision(
            decision=decision,
            content=content or fallback_content,
            rationale=rationale,
        )

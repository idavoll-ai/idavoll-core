"""Review team — parallel reviewer subagents + moderator (Phase 2).

Flow (§6–7, review_full_design.md):

  ReviewTeam.review_agent_in_topic()
    -> _build_reviewer_specs()           one SubagentSpec per role
    -> SubagentRuntime._run_subagents_in_parallel()
    -> _parse_reviewer_outputs()         structured JSON parsing + fallback
    -> _moderate()                       direct LLM call, produces ReviewOutcome
    -> _make_directives()                GrowthDirective list from outcome

Reviewer subagents are ephemeral, inherit_parent_tools=False (pure analysts).
Moderator is a direct LLM call on app.llm — not a subagent — so it can
produce directives without violating the memory-write restriction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from idavoll.subagent.models import SubagentSpec, SubagentResult
from vingolf.config import (
    ReviewRoleConfig,
    ReviewPlanConfig,
    ReviewTargetType,
    default_review_role_catalog,
)

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp

    from .review import Post, Topic
    from vingolf.config import ReviewConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class GrowthDirective(BaseModel):
    """A single actionable output produced by the review system.

    Instead of writing raw review summaries directly into MEMORY.md, the
    review pipeline produces GrowthDirectives.  A downstream consolidation
    process (Phase 3) decides which directives are promoted into long-term
    agent memory or policy.
    """

    kind: Literal[
        "memory_candidate",
        "policy_candidate",
        "reflection_candidate",
        "no_action",
    ]
    priority: Literal["low", "medium", "high"]
    content: str
    rationale: str
    ttl_days: int | None = None


class ReviewerOutput(BaseModel):
    """Structured output from a single reviewer subagent."""

    role: str
    dimension: str
    status: Literal["ok", "timeout", "failed"] = "ok"
    score: float = 5.0          # 1–10
    confidence: float = 0.5     # 0–1
    evidence: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    summary: str = ""
    raw_output: str = ""
    parse_failed: bool = False  # True when LLM output could not be parsed


class ReviewOutcome(BaseModel):
    """Aggregated judgment produced by the Moderator."""

    quality_score: float = 5.0
    confidence: float = 0.5
    summary: str = ""
    key_strengths: list[str] = Field(default_factory=list)
    key_weaknesses: list[str] = Field(default_factory=list)
    growth_priority: Literal["low", "medium", "high"] = "low"
    growth_directives: list[GrowthDirective] = Field(default_factory=list)


class ReviewRecord(BaseModel):
    """Full audit record of one review execution."""

    review_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic_id: str
    session_id: str | None = None
    agent_id: str
    agent_name: str
    target_type: Literal["agent_in_topic", "post", "thread"] = "agent_in_topic"
    target_id: str | None = None
    status: Literal["completed", "failed"] = "completed"
    error_message: str | None = None
    reviewer_outputs: list[ReviewerOutput] = Field(default_factory=list)
    outcome: ReviewOutcome = Field(default_factory=ReviewOutcome)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @model_validator(mode="after")
    def _populate_target_id(self) -> "ReviewRecord":
        if self.target_id:
            return self
        if self.target_type == "agent_in_topic":
            self.target_id = self.agent_id
            return self
        raise ValueError("target_id is required for post/thread reviews")


# Built-in defaults, also used by tests as the baseline catalog.
REVIEWER_ROLES: dict[str, ReviewRoleConfig] = default_review_role_catalog()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REVIEWER_SYSTEM_TMPL = """\
你是 {role}，负责从「{dimension}」维度评估 Agent 在话题讨论中的表现。

【评估标准】
{criteria}

【输出格式】
只返回 JSON，不含其他文字：
{{"score": <float 1-10>, "confidence": <float 0-1>, "evidence": ["...", "..."], "concerns": ["..."], "summary": "<不超过80字>"}}

字段说明：
- score: 该维度的综合评分（1-10）
- confidence: 你对该判断的把握程度（0-1）
- evidence: 支撑判断的具体内容或行为（2-4条，可以是帖子原文片段）
- concerns: 值得关注的问题（0-3条，无则为空列表）
- summary: 一句话总结（不超过80字）\
"""

_LEAD_PLANNER_SYSTEM = """\
你是 LeadReviewer / Review Planner。

你的任务不是亲自完成评审，而是根据当前目标类型、候选 reviewer roles 及上下文，
选择这次最合适的 reviewer 子集。

要求：
- 只从给定的 candidate roles 中选择
- 优先选择“足够但不过度”的 reviewer 组合
- 默认不必把所有 role 全选
- 如果目标是 post / thread，只有在分支讨论质量真的重要时再优先加入 ThreadReviewer
- 如果目标是 agent_in_topic，通常优先覆盖深度、互动、安全
- 如果你不确定，可以给出保守但完整的组合

只返回 JSON，不含其他文字：
{"selected_roles":["RoleA","RoleB"],"rationale":"<不超过120字>"}\
"""

_MODERATOR_SYSTEM = """\
你是 Moderator，负责综合多个评审员的意见，形成最终判断。

规则：
- 当评审员之间评分分歧较大时，confidence 应相应降低
- key_strengths / key_weaknesses 来自各评审员的 evidence 和 concerns
- growth_priority 参考 quality_score：>= 8 → high, 6–8 → medium, < 6 → low

只返回 JSON，不含其他文字：
{"quality_score": <float 1-10>, "confidence": <float 0-1>, "summary": "<string>", \
"key_strengths": ["...", "..."], "key_weaknesses": ["..."], "growth_priority": "<low|medium|high>"}\
"""


# ---------------------------------------------------------------------------
# ReviewTeam
# ---------------------------------------------------------------------------

class ReviewTeam:
    """Orchestrates reviewer subagents and a Moderator for one agent review.

    Usage::

        team = ReviewTeam(app, orchestrator_agent, config)
        outcome, records = await team.review_agent_in_topic(
            topic, agent_name, agent_id, agent_posts, all_posts_text
        )
    """

    def __init__(
        self,
        app: "IdavollApp",
        orchestrator_agent: "Agent",
        config: "ReviewConfig",
        plan_config: "ReviewPlanConfig | None" = None,
    ) -> None:
        self._app = app
        self._orchestrator = orchestrator_agent
        self._config = config
        self._plan_config = plan_config or ReviewPlanConfig()

    async def review_agent_in_topic(
        self,
        topic: "Topic",
        agent_name: str,
        agent_id: str,
        agent_posts: "list[Post]",
        all_posts_text: str,
    ) -> tuple[ReviewOutcome, list[ReviewerOutput]]:
        """Run the full review team pipeline for one agent in one topic."""
        context_bundle = self._build_agent_context_bundle(
            topic, agent_name, agent_posts, all_posts_text
        )

        # 1. Dispatch reviewer subagents in parallel.
        selected_roles = await self._select_reviewer_roles(
            "agent_in_topic",
            subject_label=f"Agent 「{agent_name}」",
            planning_context=context_bundle,
        )
        specs = self._build_reviewer_specs(
            context_bundle, f"Agent 「{agent_name}」", selected_roles
        )
        raw_results = await self._app.subagent_runtime._run_subagents_in_parallel(
            self._orchestrator, specs
        )

        # 2. Parse reviewer outputs (failed parses produce fallback ReviewerOutput).
        reviewer_outputs = [
            self._parse_reviewer_output(result, role)
            for result, role in zip(raw_results, [name for name, _ in selected_roles])
        ]

        # 3. Moderator aggregates into ReviewOutcome.
        outcome = await self._moderate(reviewer_outputs, topic.title, agent_name)

        # 4. Produce GrowthDirectives from outcome.
        outcome.growth_directives = self._make_directives(
            outcome, topic.title
        )

        return outcome, reviewer_outputs

    async def review_post_in_topic(
        self,
        topic: "Topic",
        post: "Post",
        context_posts: "list[Post]",
    ) -> tuple[ReviewOutcome, list[ReviewerOutput]]:
        """Run the review team pipeline for one focused post in a topic."""
        context_bundle = self._build_post_context_bundle(topic, post, context_posts)

        selected_roles = await self._select_reviewer_roles(
            "post",
            subject_label=f"帖子（作者：{post.author_name}）",
            planning_context=context_bundle,
        )
        specs = self._build_reviewer_specs(
            context_bundle, f"帖子（作者：{post.author_name}）", selected_roles
        )
        raw_results = await self._app.subagent_runtime._run_subagents_in_parallel(
            self._orchestrator, specs
        )

        reviewer_outputs = [
            self._parse_reviewer_output(result, role)
            for result, role in zip(raw_results, [name for name, _ in selected_roles])
        ]

        outcome = await self._moderate(reviewer_outputs, topic.title, post.author_name)
        outcome.growth_directives = self._make_directives(outcome, topic.title)
        return outcome, reviewer_outputs

    # ------------------------------------------------------------------
    # Spec construction
    # ------------------------------------------------------------------

    async def _select_reviewer_roles(
        self,
        target_type: ReviewTargetType,
        *,
        subject_label: str,
        planning_context: str,
    ) -> list[tuple[str, ReviewRoleConfig]]:
        """Select reviewer roles for this target, preferably via lead-agent planning."""
        compatible = self._compatible_reviewer_roles(target_type)
        if not self._plan_config.use_lead_planner:
            return compatible

        planned = await self._plan_reviewer_roles_with_lead(
            target_type,
            subject_label=subject_label,
            planning_context=planning_context,
            compatible_roles=compatible,
        )
        return planned or compatible

    def _compatible_reviewer_roles(
        self,
        target_type: ReviewTargetType,
    ) -> list[tuple[str, ReviewRoleConfig]]:
        """Deterministic fallback: return configured compatible roles."""
        catalog = self._plan_config.reviewer_roles
        preferred_names = {
            "agent_in_topic": self._plan_config.default_roles_for_agent_in_topic,
            "post": self._plan_config.default_roles_for_post,
            "thread": self._plan_config.default_roles_for_thread,
        }[target_type]

        selected: list[tuple[str, ReviewRoleConfig]] = []
        for name in preferred_names:
            role_spec = catalog.get(name)
            if role_spec is None:
                logger.warning(
                    "configured reviewer role %r missing from catalog; skipping", name
                )
                continue
            if not role_spec.enabled:
                logger.debug("reviewer role %r disabled; skipping", name)
                continue
            if target_type not in role_spec.target_types:
                logger.debug(
                    "reviewer role %r does not support target_type=%r; skipping",
                    name,
                    target_type,
                )
                continue
            selected.append((name, role_spec))

        if selected:
            return selected

        fallback = [
            (name, role_spec)
            for name, role_spec in catalog.items()
            if role_spec.enabled and target_type in role_spec.target_types
        ]
        if fallback:
            logger.warning(
                "no preferred reviewer roles available for target_type=%r; "
                "falling back to all compatible enabled roles",
                target_type,
            )
            return fallback

        raise ValueError(
            f"No enabled reviewer roles available for target_type={target_type!r}"
        )

    async def _plan_reviewer_roles_with_lead(
        self,
        target_type: ReviewTargetType,
        *,
        subject_label: str,
        planning_context: str,
        compatible_roles: list[tuple[str, ReviewRoleConfig]],
    ) -> list[tuple[str, ReviewRoleConfig]]:
        """Ask the lead/orchestrator agent to choose a reviewer subset."""
        compatible_map = {name: spec for name, spec in compatible_roles}
        candidate_lines = []
        for name, spec in compatible_roles:
            candidate_lines.append(
                f"- {name}: dimension={spec.dimension}; "
                f"target_types={','.join(spec.target_types)}\n"
                f"  criteria:\n{spec.criteria}"
            )

        prompt = (
            f"Target Type: {target_type}\n"
            f"Subject: {subject_label}\n"
            f"Max Selected Roles: {self._plan_config.lead_max_selected_roles}\n\n"
            f"Candidate Roles:\n" + "\n".join(candidate_lines) + "\n\n"
            f"Context Excerpt:\n{planning_context[:3000]}"
        )

        original_tools = self._orchestrator.tools
        self._orchestrator.tools = []
        try:
            raw = await asyncio.wait_for(
                self._app.generate_response(
                    self._orchestrator,
                    session=None,
                    current_message=prompt,
                    system_message=_LEAD_PLANNER_SYSTEM,
                ),
                timeout=self._plan_config.lead_planner_timeout_seconds,
            )
        except Exception:
            logger.warning(
                "lead planner failed for target_type=%r; falling back to deterministic roles",
                target_type,
                exc_info=True,
            )
            return []
        finally:
            self._orchestrator.tools = original_tools

        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        start = cleaned.find("{")
        if start > 0:
            cleaned = cleaned[start:]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "lead planner returned unparseable JSON for target_type=%r; falling back",
                target_type,
            )
            return []

        selected_names = data.get("selected_roles", [])
        if not isinstance(selected_names, list):
            logger.warning(
                "lead planner returned invalid selected_roles for target_type=%r; falling back",
                target_type,
            )
            return []

        deduped: list[str] = []
        for name in selected_names:
            role_name = str(name).strip()
            if role_name in compatible_map and role_name not in deduped:
                deduped.append(role_name)
            if len(deduped) >= self._plan_config.lead_max_selected_roles:
                break

        if not deduped:
            logger.warning(
                "lead planner selected no compatible roles for target_type=%r; falling back",
                target_type,
            )
            return []

        rationale = str(data.get("rationale", "")).strip()
        if rationale:
            logger.info(
                "lead planner selected roles for %s: %s | rationale=%s",
                target_type,
                ", ".join(deduped),
                rationale,
            )
        return [(name, compatible_map[name]) for name in deduped]

    def _build_reviewer_specs(
        self,
        context_bundle: str,
        subject_label: str,
        selected_roles: list[tuple[str, ReviewRoleConfig]],
    ) -> list[SubagentSpec]:
        specs: list[SubagentSpec] = []
        for role, role_spec in selected_roles:
            system_instruction = _REVIEWER_SYSTEM_TMPL.format(
                role=role,
                dimension=role_spec.dimension,
                criteria=role_spec.criteria,
            )
            specs.append(SubagentSpec(
                goal=f"评估{subject_label}在以下话题讨论中的「{role_spec.dimension}」表现",
                context=context_bundle,
                role=role,
                system_instruction=system_instruction,
                inherit_parent_tools=False,   # pure LLM analyst, no tools needed
                memory_mode="disabled",
                max_turns=1,
                timeout_seconds=(
                    role_spec.timeout_seconds
                    if role_spec.timeout_seconds is not None
                    else self._config.reviewer_timeout_seconds
                ),
            ))
        return specs

    def _build_agent_context_bundle(
        self,
        topic: "Topic",
        agent_name: str,
        agent_posts: "list[Post]",
        all_posts_text: str,
    ) -> str:
        agent_posts_text = "\n".join(
            f"{i}. {p.content[: self._config.max_post_chars]}"
            for i, p in enumerate(agent_posts, 1)
        )
        return (
            f"话题标题：{topic.title}\n"
            f"话题描述：{topic.description}\n\n"
            f"---- 完整讨论摘要 ----\n{all_posts_text}\n\n"
            f"---- 待评 Agent：{agent_name} ----\n{agent_posts_text}"
        )

    def _build_post_context_bundle(
        self,
        topic: "Topic",
        post: "Post",
        context_posts: "list[Post]",
    ) -> str:
        context_text = "\n".join(
            f"{i}. [{p.author_name}] {p.content[: self._config.max_post_chars]}"
            for i, p in enumerate(context_posts, 1)
        )
        return (
            f"话题标题：{topic.title}\n"
            f"话题描述：{topic.description}\n\n"
            f"---- 相关讨论分支 ----\n{context_text}\n\n"
            f"---- 待评帖子 ----\n"
            f"作者：{post.author_name}\n"
            f"点赞数：{post.likes}\n"
            f"内容：{post.content[: self._config.max_post_chars]}"
        )

    # ------------------------------------------------------------------
    # Parsing 解析器，将原始 LLM 输出转换成结构化数据，包含健壮的降级逻辑
    # ------------------------------------------------------------------

    def _parse_reviewer_output(
        self, result: SubagentResult, role: str
    ) -> ReviewerOutput:
        role_spec = self._plan_config.reviewer_roles[role]

        if result.status != "ok" or not result.output_text:
            logger.warning(
                "reviewer %r returned status=%r; using fallback output",
                role, result.status,
            )
            return ReviewerOutput(
                role=role,
                dimension=role_spec.dimension,
                status=result.status,
                parse_failed=True,
                raw_output=result.output_text,
                summary=f"{role} 未能完成评审（{result.status}）",
            )

        raw = result.output_text
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        start = cleaned.find("{")
        if start > 0:
            cleaned = cleaned[start:]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("reviewer %r returned unparseable JSON", role)
            return ReviewerOutput(
                role=role,
                dimension=role_spec.dimension,
                status=result.status,
                parse_failed=True,
                raw_output=raw,
                summary=raw[:80],
            )

        def _clamp(key: str, lo: float, hi: float, default: float) -> float:
            try:
                return max(lo, min(hi, float(data.get(key, default))))
            except (TypeError, ValueError):
                return default

        return ReviewerOutput(
            role=role,
            dimension=role_spec.dimension,
            status=result.status,
            score=_clamp("score", 1.0, 10.0, 5.0),
            confidence=_clamp("confidence", 0.0, 1.0, 0.5),
            evidence=list(data.get("evidence", [])),
            concerns=list(data.get("concerns", [])),
            summary=str(data.get("summary", "")).strip()[:120],
            raw_output=raw,
        )

    # ------------------------------------------------------------------
    # Moderator aggregation 用来聚合评审员意见形成最终判断
    # ------------------------------------------------------------------

    async def _moderate(
        self,
        reviewer_outputs: list[ReviewerOutput],
        topic_title: str,
        agent_name: str,
    ) -> ReviewOutcome:
        """Run the Moderator LLM call and parse the result."""
        outputs_json = json.dumps(
            [o.model_dump() for o in reviewer_outputs],
            ensure_ascii=False,
            indent=2,
        )
        prompt = (
            f"话题：{topic_title}\n"
            f"Agent：{agent_name}\n\n"
            f"评审员输出：\n{outputs_json}"
        )
        try:
            raw = await self._app.llm.generate(
                [
                    SystemMessage(content=_MODERATOR_SYSTEM),
                    HumanMessage(content=prompt),
                ],
                run_name="review-moderator",
                metadata={"topic": topic_title, "agent": agent_name},
            )
            return self._parse_outcome(raw, reviewer_outputs)
        except Exception:
            logger.warning(
                "Moderator LLM call failed for %r in %r; using fallback",
                agent_name, topic_title, exc_info=True,
            )
            return self._fallback_outcome(reviewer_outputs)

    @staticmethod
    def _parse_outcome(
        raw: str, reviewer_outputs: list[ReviewerOutput]
    ) -> ReviewOutcome:
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        start = cleaned.find("{")
        if start > 0:
            cleaned = cleaned[start:]
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return ReviewTeam._fallback_outcome(reviewer_outputs)

        def _clamp(key, lo, hi, default):
            try:
                return max(lo, min(hi, float(data.get(key, default))))
            except (TypeError, ValueError):
                return default

        priority_raw = str(data.get("growth_priority", "low")).lower()
        priority: Literal["low", "medium", "high"] = (
            priority_raw if priority_raw in ("low", "medium", "high") else "low"
        )
        return ReviewOutcome(
            quality_score=_clamp("quality_score", 1.0, 10.0, 5.0),
            confidence=_clamp("confidence", 0.0, 1.0, 0.5),
            summary=str(data.get("summary", "")).strip()[:200],
            key_strengths=list(data.get("key_strengths", [])),
            key_weaknesses=list(data.get("key_weaknesses", [])),
            growth_priority=priority,
        )

    @staticmethod
    def _fallback_outcome(reviewer_outputs: list[ReviewerOutput]) -> ReviewOutcome:
        """Deterministic fallback when Moderator LLM fails."""
        valid = [o for o in reviewer_outputs if not o.parse_failed]
        if not valid:
            return ReviewOutcome(confidence=0.0)
        avg_score = sum(o.score for o in valid) / len(valid)
        avg_conf = sum(o.confidence for o in valid) / len(valid)
        # Lower confidence when reviews diverge.
        if len(valid) > 1:
            import statistics
            stdev = statistics.stdev(o.score for o in valid)
            avg_conf *= max(0.3, 1.0 - stdev / 5.0)
        priority: Literal["low", "medium", "high"] = (
            "high" if avg_score >= 8.0 else "medium" if avg_score >= 6.0 else "low"
        )
        return ReviewOutcome(
            quality_score=round(avg_score, 2),
            confidence=round(avg_conf, 2),
            summary="（Moderator 降级：各评审员平均值）",
            growth_priority=priority,
        )

    # ------------------------------------------------------------------
    # Directive generation
    # ------------------------------------------------------------------

    def _make_directives(
        self, outcome: ReviewOutcome, topic_title: str
    ) -> list[GrowthDirective]:
        """Convert a ReviewOutcome into GrowthDirectives.

        Uses outcome.growth_priority and outcome.confidence to decide kind
        and priority, replacing the Phase 0 score-threshold heuristic.
        """
        context = f"「{topic_title}」" if topic_title else "话题"

        if outcome.confidence < 0.4:
            return [GrowthDirective(
                kind="no_action",
                priority="low",
                content="",
                rationale=f"Moderator 置信度过低（{outcome.confidence:.2f}），不产生有效 directive。",
            )]

        directives: list[GrowthDirective] = []

        # Strengths → memory candidate
        if outcome.key_strengths and outcome.growth_priority in ("medium", "high"):
            directives.append(GrowthDirective(
                kind="memory_candidate",
                priority=outcome.growth_priority,
                content=outcome.summary or "; ".join(outcome.key_strengths[:2]),
                rationale=(
                    f"{context}综合得分 {outcome.quality_score:.1f}/10，"
                    f"置信度 {outcome.confidence:.2f}。"
                    f"强项：{'; '.join(outcome.key_strengths[:2])}"
                ),
                ttl_days=30,
            ))

        # Weaknesses → reflection candidate
        if outcome.key_weaknesses:
            directives.append(GrowthDirective(
                kind="reflection_candidate",
                priority="medium" if outcome.growth_priority == "low" else outcome.growth_priority,
                content="; ".join(outcome.key_weaknesses[:3]),
                rationale=(
                    f"{context}发现以下待改进点："
                    f"{'; '.join(outcome.key_weaknesses[:2])}"
                ),
                ttl_days=14,
            ))

        if not directives:
            directives.append(GrowthDirective(
                kind="no_action",
                priority="low",
                content="",
                rationale=f"{context}评审完成，无明确 directive。",
            ))

        return directives

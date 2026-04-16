"""Request and response schemas for the Vingolf HTTP API.

All models are pure Pydantic; they carry no business logic.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class CreateAgentRequest(BaseModel):
    name: str = Field(..., description="显示名称")
    description: str = Field(..., description="自然语言人格描述，用于生成 SOUL.md")
    soul: str | None = Field(
        default=None,
        description="用户已确认的 SOUL.md 文本；提供时将直接用于创建 Agent，而不再从 description 重新生成人格。",
    )


class RefineSoulRequest(BaseModel):
    feedback: str = Field(..., description="对当前 SOUL.md 草稿的修改意见")


class RefineSoulTextRequest(BaseModel):
    name: str = Field(..., description="Agent 名称")
    current_soul: str = Field(..., description="当前 SOUL.md 文本")
    feedback: str = Field(..., description="修改意见")


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    level: int = 1
    xp: int = 0
    context_budget: int


class SoulPreviewOut(BaseModel):
    soul: str = Field(..., description="当前 SOUL.md 的 Markdown 原文")


# ---------------------------------------------------------------------------
# Topic
# ---------------------------------------------------------------------------

class CreateTopicRequest(BaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)


class JoinTopicRequest(BaseModel):
    agent_id: str


class AddUserPostRequest(BaseModel):
    author_name: str = Field(default="User")
    content: str
    reply_to: str | None = Field(default=None, description="被回复的帖子 ID")


class ParticipateRequest(BaseModel):
    agent_id: str = Field(..., description="发起一次参与决策的 Agent ID")


class MultiParticipateRequest(BaseModel):
    agent_id: str = Field(..., description="Agent ID")
    rounds: int = Field(default=3, ge=1, le=20, description="让该 Agent 连续参与的轮数（1–20）")


class PostOut(BaseModel):
    id: str
    topic_id: str
    author_id: str
    author_name: str
    content: str
    source: str
    reply_to: str | None
    likes: int


class TopicOut(BaseModel):
    id: str
    title: str
    description: str
    tags: list[str]
    lifecycle: str
    member_count: int


class DecisionOut(BaseModel):
    topic_id: str
    agent_id: str
    action: str   # "ignore" | "reply" | "post"
    reason: str
    post_id: str | None


# ---------------------------------------------------------------------------
# Review & Progress
# ---------------------------------------------------------------------------

class DimensionScoresOut(BaseModel):
    relevance: float
    depth: float
    originality: float
    engagement: float
    average: float


class GrowthDirectiveOut(BaseModel):
    kind: str          # memory_candidate | reflection_candidate | no_action | policy_candidate
    priority: str      # low | medium | high
    content: str
    rationale: str
    agent_decision: str | None = None
    decision_rationale: str | None = None
    final_content: str | None = None
    decided_at: str | None = None
    ttl_days: int | None = None


class ReviewStrategyResultOut(BaseModel):
    reviewer_name: str
    status: str
    dimension: str
    score: float
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    parse_failed: bool = False
    summary: str
    raw_output: str = ""


class AgentReviewResultOut(BaseModel):
    agent_id: str
    agent_name: str
    post_count: int
    likes_count: int
    composite_score: float
    likes_score: float
    final_score: float
    dimensions: DimensionScoresOut
    summary: str
    # Phase 2/3 fields
    confidence: float = 1.0
    evidence: list[str] = Field(default_factory=list)
    growth_directives: list[GrowthDirectiveOut] = Field(default_factory=list)


class TopicReviewSummaryOut(BaseModel):
    topic_id: str
    topic_title: str
    results: list[AgentReviewResultOut]


class ReviewRecordOut(BaseModel):
    id: str
    trigger_type: str
    topic_id: str
    session_id: str | None = None
    target_type: str
    target_id: str
    agent_id: str
    agent_name: str
    quality_score: float
    confidence: float
    summary: str
    growth_priority: str
    status: str
    error_message: str | None = None
    created_at: str
    strategy_results: list[ReviewStrategyResultOut] = Field(default_factory=list)
    growth_directives: list[GrowthDirectiveOut] = Field(default_factory=list)


class AgentProgressOut(BaseModel):
    agent_id: str
    xp: int
    level: int


# ---------------------------------------------------------------------------
# Bootstrap conversation (对话式创建 SOUL.md)
# ---------------------------------------------------------------------------

class BootstrapMessage(BaseModel):
    role: str = Field(..., description='"user" 或 "assistant"')
    content: str


class BootstrapChatRequest(BaseModel):
    name: str = Field(..., description="Agent 名称")
    messages: list[BootstrapMessage] = Field(..., description="当前对话历史（含本次用户消息）")


class BootstrapChatResponse(BaseModel):
    reply: str = Field(..., description="AI 的回复文本")
    soul: str | None = Field(None, description="若 AI 认为信息充足，返回生成的 SOUL.md；否则为 null")


# ---------------------------------------------------------------------------
# Agent ↔ Topic membership
# ---------------------------------------------------------------------------

class MembershipOut(BaseModel):
    joined_at: str = Field(..., description="ISO-8601 UTC 时间戳")
    initiative_posts: int = Field(..., description="主动发言次数")
    reply_posts: int = Field(..., description="回复发言次数")
    last_post_at: str | None = Field(None, description="最后发言时间")


class AgentTopicOut(BaseModel):
    """Agent 已加入的话题 + 该 Agent 在该话题中的参与状态。"""
    id: str
    title: str
    description: str
    tags: list[str]
    lifecycle: str
    member_count: int
    membership: MembershipOut

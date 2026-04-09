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


class RefineSoulRequest(BaseModel):
    feedback: str = Field(..., description="对当前 SOUL.md 草稿的修改意见")


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
    agent_ids: list[str] = Field(
        default_factory=list,
        description="预先加入话题的 Agent ID 列表（可留空，之后再 join）",
    )
    tags: list[str] = Field(default_factory=list)


class JoinTopicRequest(BaseModel):
    agent_id: str


class AddUserPostRequest(BaseModel):
    author_name: str = Field(default="User")
    content: str
    reply_to: str | None = Field(default=None, description="被回复的帖子 ID")


class ParticipateRequest(BaseModel):
    agent_id: str = Field(..., description="发起一次参与决策的 Agent ID")


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


class TopicReviewSummaryOut(BaseModel):
    topic_id: str
    topic_title: str
    results: list[AgentReviewResultOut]


class AgentProgressOut(BaseModel):
    agent_id: str
    xp: int
    level: int

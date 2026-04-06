from __future__ import annotations

from pydantic import BaseModel, Field


# ── LLM structured output targets ────────────────────────────────────────────

class DimensionScore(BaseModel):
    """What one reviewer returns for a single agent."""
    score: int = Field(..., ge=1, le=10)
    reasoning: str = Field(..., description="Why this score was given")
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-3 specific observations from the posts",
    )


class NegotiatedScores(BaseModel):
    """
    Output of the negotiation phase.
    A moderator sees all four independent reviews and reaches a consensus.
    """
    logic_score: int = Field(..., ge=1, le=10)
    creativity_score: int = Field(..., ge=1, le=10)
    social_score: int = Field(..., ge=1, le=10)
    persona_consistency_score: int = Field(
        ..., ge=1, le=10,
        description="How consistently the agent stayed in character throughout",
    )
    summary: str = Field(..., description="One paragraph overall assessment")
    adjustment_notes: str = Field(
        ...,
        description="What changed from the independent scores and why, or 'No adjustments made'",
    )


# ── Domain result models ──────────────────────────────────────────────────────

class AgentReviewResult(BaseModel):
    """Final review outcome for one agent in a topic."""
    agent_id: str
    agent_name: str

    # Raw dimension scores (post-negotiation, 1-10)
    logic_score: float
    creativity_score: float
    social_score: float
    persona_consistency_score: float

    # Weighted combination of the four dimensions (equal weights, 1-10)
    composite_score: float

    # Likes data
    likes_count: int
    likes_score: float   # likes normalized to 1-10 within this topic

    # Final score: 50% composite + 50% likes
    final_score: float

    post_count: int
    summary: str          # From negotiation phase
    adjustment_notes: str


class TopicReviewSummary(BaseModel):
    """Aggregated review results for an entire topic."""
    topic_id: str
    topic_title: str
    results: list[AgentReviewResult]

    def winner(self) -> AgentReviewResult | None:
        if not self.results:
            return None
        return max(self.results, key=lambda r: r.final_score)

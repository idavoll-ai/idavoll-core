from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class ReviewConfig(BaseModel):
    """Settings for the ReviewPlugin.

    Weight semantics
    ----------------
    ``composite_weight`` governs the "content quality" dimension — either the
    LLM-evaluated score (when ``use_llm=True`` and the call succeeds) or the
    deterministic post-count formula used as a fallback.
    ``likes_weight`` governs community engagement (accumulated likes).
    The two must sum to 1.0.
    """

    max_post_chars: int = Field(
        default=3000,
        description="Max characters of agent posts fed into the LLM context per review call.",
    )
    composite_weight: float = Field(default=0.6)
    likes_weight: float = Field(default=0.4)
    use_llm: bool = Field(
        default=True,
        description=(
            "When True, the ReviewPlugin calls the LLM to produce multi-dimensional "
            "content scores.  Falls back to deterministic scoring on any LLM error."
        ),
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ReviewConfig":
        total = self.composite_weight + self.likes_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"composite_weight + likes_weight must equal 1.0, got {total}"
            )
        return self


class TopicConfig(BaseModel):
    """Parameters for TopicPlugin — tuned for event-driven discussion."""

    # Seed posting: how many initiative posts to publish before closing
    num_seeds: int = Field(
        default=5,
        description="Number of initiative posts TopicPlugin publishes to seed the discussion",
    )
    seed_interval: float = Field(
        default=1.0,
        description="Seconds to wait between seed posts",
    )
    max_context_messages: int = Field(
        default=20,
        description="How many recent posts are visible to agents in their context window",
    )

    # Per-agent participation limits
    initiative_quota: int = Field(
        default=5,
        description="Max unprompted posts per agent per topic",
    )
    reply_quota: int = Field(
        default=10,
        description="Max reply posts per agent per topic",
    )
    cooldown_seconds: float = Field(
        default=0.0,
        description="Min seconds between any two posts by the same agent",
    )
    max_reply_depth: int = Field(
        default=3,
        description="Max reply chain depth; agents won't respond beyond this depth",
    )

    # Concurrency control
    max_concurrent_responses: int = Field(
        default=3,
        description="Max agents that can respond to a single post simultaneously",
    )

    # Capacity
    max_agents: int = Field(default=10, description="Hard cap on participants per topic")


class LevelingConfig(BaseModel):
    """Settings for the LevelingPlugin."""

    xp_per_point: int = Field(
        default=10,
        description="XP awarded per 1.0 of final_score",
    )
    base_xp_per_level: int = Field(
        default=100,
        description="XP needed to advance from level N to N+1: base * N",
    )
    budget_increment_per_level: int = Field(
        default=512,
        description="Tokens added to ContextBudget.total on each level-up",
    )


GrowthConfig = LevelingConfig


class VingolfConfig(BaseModel):
    """Top-level configuration for Vingolf plugins."""

    review: ReviewConfig = Field(default_factory=ReviewConfig)
    topic: TopicConfig = Field(default_factory=TopicConfig)
    leveling: LevelingConfig = Field(default_factory=LevelingConfig)
    db_path: str = Field(
        default="vingolf.db",
        description="Path to the SQLite database file. Relative paths are resolved from CWD.",
    )

    @model_validator(mode="before")
    @classmethod
    def _support_growth_alias(cls, data):
        if isinstance(data, dict) and "growth" in data and "leveling" not in data:
            data = dict(data)
            data["leveling"] = data.pop("growth")
        return data

    @classmethod
    def from_yaml(cls, path: str | Path) -> "VingolfConfig":
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "pyyaml is required to load config from YAML. "
                "Install it with: pip install pyyaml"
            ) from exc
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data.get("vingolf", data))

    @classmethod
    def defaults(cls) -> "VingolfConfig":
        return cls()

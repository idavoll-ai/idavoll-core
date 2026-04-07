from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class ReviewConfig(BaseModel):
    """Settings for the ReviewPlugin."""

    max_post_chars: int = 3000
    composite_weight: float = 0.5
    likes_weight: float = 0.5

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ReviewConfig":
        total = self.composite_weight + self.likes_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"composite_weight + likes_weight must equal 1.0, got {total}"
            )
        return self


class TopicConfig(BaseModel):
    """Default parameters used by TopicPlugin.create_topic / start_discussion."""

    default_rounds: int = 10
    min_interval: float = 1.0
    max_agents: int = 10
    max_context_messages: int = 20
    strategy: str = Field(
        default="relevance",
        description="Scheduling strategy: 'relevance' | 'round_robin' | 'random'",
    )
    per_agent_max_turns: int | None = Field(
        default=None,
        description="Max times a single agent may speak per topic. None = unlimited.",
    )


class GrowthConfig(BaseModel):
    """Settings for the GrowthPlugin."""

    xp_per_point: int = Field(
        default=10,
        description="XP awarded per 1.0 of final_score (e.g. score=7.5 → 75 XP)",
    )
    base_xp_per_level: int = Field(
        default=100,
        description="XP needed to advance from level N to N+1: base * N",
    )
    budget_increment_per_level: int = Field(
        default=512,
        description="Tokens added to ContextBudget.total on each level-up",
    )


class VingolfConfig(BaseModel):
    """Top-level configuration for Vingolf plugins."""

    review: ReviewConfig = Field(default_factory=ReviewConfig)
    topic: TopicConfig = Field(default_factory=TopicConfig)
    growth: GrowthConfig = Field(default_factory=GrowthConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "VingolfConfig":
        """Load config from a YAML file (requires pyyaml)."""
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
        """Return a config object with all defaults applied."""
        return cls()

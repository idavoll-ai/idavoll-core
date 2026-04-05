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


class VingolfConfig(BaseModel):
    """Top-level configuration for Vingolf plugins."""

    review: ReviewConfig = Field(default_factory=ReviewConfig)
    topic: TopicConfig = Field(default_factory=TopicConfig)

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

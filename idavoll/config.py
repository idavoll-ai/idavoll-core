from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

class LLMConfig(BaseModel):
    """Settings for the language model."""

    provider: Literal["anthropic", "openai", "deepseek", "kimi"] = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.7
    max_tokens: int = 1024
    base_url: str | None = None
    # API key from config file; runtime-supplied key takes precedence.
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _require_base_url_for_compat_providers(self) -> "LLMConfig":
        if self.provider != "anthropic" and not self.base_url:
            raise ValueError(
                f"provider={self.provider!r} requires base_url to be set in config"
            )
        return self

    def build(self, api_key: str | None = None):
        """Construct the LangChain chat model from this config.

        ``api_key`` passed here takes precedence over the value in config,
        which in turn takes precedence over the provider's environment variable.
        """
        resolved_key: str | None = api_key or (
            self.api_key.get_secret_value() if self.api_key else None
        )

        if self.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            kwargs: dict = {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if resolved_key is not None:
                kwargs["api_key"] = resolved_key
            return ChatAnthropic(**kwargs)

        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "base_url": self.base_url,
        }
        if resolved_key is not None:
            kwargs["api_key"] = resolved_key
        return ChatOpenAI(**kwargs)


class SessionConfig(BaseModel):
    """Default parameters for session lifecycle."""

    default_rounds: int = 10
    min_interval: float = 1.0
    max_context_messages: int = 20


class SchedulerConfig(BaseModel):
    """Scheduling strategy used when no plugin overrides it."""

    strategy: Literal["round_robin", "random"] = "round_robin"

    def build(self):
        """Construct the scheduler strategy from this config."""
        from .scheduler.strategies import RandomStrategy, RoundRobinStrategy

        if self.strategy == "round_robin":
            return RoundRobinStrategy()
        if self.strategy == "random":
            return RandomStrategy()
        raise ValueError(f"Unknown scheduler strategy: {self.strategy!r}")


class IdavollConfig(BaseModel):
    """Top-level configuration for an IdavollApp instance."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "IdavollConfig":
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
        return cls.model_validate(data.get("idavoll", data))

    @classmethod
    def defaults(cls) -> "IdavollConfig":
        """Return a config object with all defaults applied."""
        return cls()

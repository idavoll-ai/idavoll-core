from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator


class LLMConfig(BaseModel):
    """Settings for the underlying chat model."""

    provider: Literal["anthropic", "openai", "deepseek", "kimi", "siliconflow"] = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.7
    max_tokens: int = 1024
    base_url: str | None = None
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _require_base_url_for_compat_providers(self) -> "LLMConfig":
        if self.provider != "anthropic" and not self.base_url:
            raise ValueError(
                f"provider={self.provider!r} requires base_url to be set in config"
            )
        return self

    def build(self, api_key: str | None = None):
        resolved_key = api_key or (
            self.api_key.get_secret_value() if self.api_key is not None else None
        )

        if self.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            kwargs: dict[str, object] = {
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
    default_rounds: int = 10
    min_interval: float = 0.0
    max_context_messages: int = 20


class SchedulerConfig(BaseModel):
    """Generic job scheduling limits, not business-level decision policies."""

    max_concurrent_jobs: int = Field(
        default=16,
        description="Maximum number of coroutines dispatched concurrently.",
    )
    default_cooldown_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Default per-agent cooldown applied when dispatch() callers do not "
            "supply an explicit cooldown_seconds argument. 0 means opt-in only."
        ),
    )
    background_shutdown_timeout: float = Field(
        default=5.0,
        ge=0.0,
        description="Seconds to wait for background tasks during graceful shutdown.",
    )


class WorkspaceConfig(BaseModel):
    """Filesystem settings for Profile Workspaces."""

    base_dir: Path = Field(
        default=Path("workspaces"),
        description="Root directory that holds all profile workspace folders.",
    )


class CompressionConfig(BaseModel):
    """Tuning knobs for the ContextCompressor."""

    enabled: bool = True
    # Compress history when its estimated token count exceeds this value.
    token_threshold: int = Field(default=2000, gt=0)
    # Always keep this many messages at the head (context anchors).
    head_keep: int = Field(default=2, ge=0)
    # Always keep this many messages at the tail (recent context).
    tail_keep: int = Field(default=6, ge=1)
    # Don't compress unless the history has at least this many messages.
    min_messages: int = Field(default=10, ge=1)


class IdavollConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    compression: CompressionConfig = Field(default_factory=CompressionConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "IdavollConfig":
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "pyyaml is required to load config from YAML. Install it with: pip install pyyaml"
            ) from exc

        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.model_validate(data.get("idavoll", data))

    @classmethod
    def defaults(cls) -> "IdavollConfig":
        return cls()

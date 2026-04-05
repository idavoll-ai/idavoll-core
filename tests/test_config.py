"""Tests for IdavollConfig and VingolfConfig."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from idavoll.config import IdavollConfig, LLMConfig, SchedulerConfig, SessionConfig
from vingolf.config import ReviewConfig, TopicConfig, VingolfConfig


class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-haiku-4-5-20251001"
        assert 0.0 <= cfg.temperature <= 2.0
        assert cfg.max_tokens > 0

    def test_override(self):
        cfg = LLMConfig(model="claude-sonnet-4-6", temperature=0.0, max_tokens=512)
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.temperature == 0.0
        assert cfg.max_tokens == 512

    @pytest.mark.parametrize("provider", ["openai", "deepseek", "kimi"])
    def test_compat_provider_requires_base_url(self, provider):
        with pytest.raises(ValidationError, match="base_url"):
            LLMConfig(provider=provider, model="some-model")

    @pytest.mark.parametrize("provider", ["openai", "deepseek", "kimi"])
    def test_compat_provider_with_base_url(self, provider):
        cfg = LLMConfig(provider=provider, model="some-model", base_url="https://example.com/v1")
        assert cfg.base_url == "https://example.com/v1"

    def test_api_key_is_secret(self):
        cfg = LLMConfig(api_key="sk-secret123")
        assert "sk-secret123" not in str(cfg)
        assert cfg.api_key.get_secret_value() == "sk-secret123"


class TestSessionConfig:
    def test_defaults(self):
        cfg = SessionConfig()
        assert cfg.default_rounds > 0
        assert cfg.min_interval >= 0
        assert cfg.max_context_messages > 0

    def test_custom(self):
        cfg = SessionConfig(default_rounds=3, min_interval=0.0, max_context_messages=5)
        assert cfg.default_rounds == 3
        assert cfg.min_interval == 0.0
        assert cfg.max_context_messages == 5


class TestSchedulerConfig:
    def test_round_robin(self):
        from idavoll.scheduler.strategies import RoundRobinStrategy
        cfg = SchedulerConfig(strategy="round_robin")
        assert isinstance(cfg.build(), RoundRobinStrategy)

    def test_random(self):
        from idavoll.scheduler.strategies import RandomStrategy
        cfg = SchedulerConfig(strategy="random")
        assert isinstance(cfg.build(), RandomStrategy)

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError):
            SchedulerConfig(strategy="nonexistent")


class TestIdavollConfig:
    def test_defaults(self):
        cfg = IdavollConfig()
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.session, SessionConfig)
        assert isinstance(cfg.scheduler, SchedulerConfig)

    def test_nested_override(self):
        cfg = IdavollConfig(llm={"model": "claude-opus-4-6"})
        assert cfg.llm.model == "claude-opus-4-6"
        # Unspecified fields still have defaults
        assert cfg.session.default_rounds > 0

    def test_from_yaml(self, tmp_path):
        yaml_content = """\
idavoll:
  llm:
    model: claude-opus-4-6
    temperature: 0.0
  session:
    default_rounds: 3
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        cfg = IdavollConfig.from_yaml(config_file)
        assert cfg.llm.model == "claude-opus-4-6"
        assert cfg.llm.temperature == 0.0
        assert cfg.session.default_rounds == 3
        # Unspecified fields use defaults
        assert cfg.session.min_interval == SessionConfig().min_interval

    def test_from_yaml_without_idavoll_key(self, tmp_path):
        """YAML without the top-level 'idavoll' key is also accepted."""
        yaml_content = """\
llm:
  model: claude-opus-4-6
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        cfg = IdavollConfig.from_yaml(config_file)
        assert cfg.llm.model == "claude-opus-4-6"

    def test_empty_yaml(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        cfg = IdavollConfig.from_yaml(config_file)
        assert cfg == IdavollConfig()


class TestReviewConfig:
    def test_defaults(self):
        cfg = ReviewConfig()
        assert cfg.max_post_chars > 0
        assert abs(cfg.composite_weight + cfg.likes_weight - 1.0) < 1e-6

    def test_custom_weights(self):
        cfg = ReviewConfig(composite_weight=0.7, likes_weight=0.3)
        assert cfg.composite_weight == 0.7

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValidationError):
            ReviewConfig(composite_weight=0.6, likes_weight=0.6)


class TestVingolfConfig:
    def test_defaults(self):
        cfg = VingolfConfig()
        assert isinstance(cfg.review, ReviewConfig)
        assert isinstance(cfg.topic, TopicConfig)

    def test_from_yaml(self, tmp_path):
        yaml_content = """\
vingolf:
  review:
    max_post_chars: 500
    composite_weight: 0.8
    likes_weight: 0.2
  topic:
    default_rounds: 4
    min_interval: 0.0
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        cfg = VingolfConfig.from_yaml(config_file)
        assert cfg.review.max_post_chars == 500
        assert cfg.review.composite_weight == 0.8
        assert cfg.topic.default_rounds == 4
        assert cfg.topic.min_interval == 0.0

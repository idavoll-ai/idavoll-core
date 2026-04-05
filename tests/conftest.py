"""
Shared fixtures for the Idavoll / Vingolf test suite.

The FakeLLM lets every test that needs an app run end-to-end
without hitting the Anthropic API.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from idavoll.agent.profile import AgentProfile
from idavoll.agent.registry import Agent, AgentRegistry


# ── Fake LLM ─────────────────────────────────────────────────────────────────

def _default_for_schema(schema: Any) -> Any:
    """
    Return a sensible default instance for common structured-output schemas
    so tests don't need to configure structured_return for every call.
    """
    from idavoll.agent.profile import _AgentProfileData
    from vingolf.plugins.review.models import DimensionScore, NegotiatedScores

    if schema is DimensionScore:
        return DimensionScore(score=7, reasoning="Looks good.", key_observations=["obs"])
    if schema is NegotiatedScores:
        return NegotiatedScores(
            logic_score=7,
            creativity_score=7,
            social_score=7,
            summary="Solid performance.",
            adjustment_notes="No adjustments made.",
        )
    if schema is _AgentProfileData:
        from idavoll.agent.profile import IdentityConfig, VoiceConfig
        return _AgentProfileData(
            identity=IdentityConfig(role="Test agent", backstory="", goal="Assist in tests"),
            voice=VoiceConfig(tone="casual", quirks=[], language="en-US"),
        )
    return None


class FakeLLM(BaseChatModel):
    """
    Deterministic stand-in for ChatAnthropic.

    Returns a fixed reply so tests never need an API key and run instantly.
    Supports ``with_structured_output`` by returning schema-appropriate defaults
    automatically. Override ``structured_return`` to force a specific value.
    """

    reply: str = "This is a test reply."
    structured_return: Any = None  # explicit override; None means auto-detect

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self, messages: list[BaseMessage], **kwargs: Any
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.reply))]
        )

    async def _agenerate(
        self, messages: list[BaseMessage], **kwargs: Any
    ) -> ChatResult:
        return self._generate(messages)

    def with_structured_output(self, schema: Any, **kwargs: Any):
        """Return a mock chain that yields an appropriate object for ``schema``."""
        explicit = self.structured_return
        chain = MagicMock()
        chain.ainvoke = self._make_ainvoke(schema, explicit)
        return chain

    @staticmethod
    def _make_ainvoke(schema: Any, explicit: Any):
        async def ainvoke(_input: Any, **_kw: Any) -> Any:
            return explicit if explicit is not None else _default_for_schema(schema)
        return ainvoke


# ── Common fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def alice_profile() -> AgentProfile:
    return AgentProfile(name="Alice")


@pytest.fixture
def bob_profile() -> AgentProfile:
    return AgentProfile(name="Bob")


@pytest.fixture
def alice(alice_profile: AgentProfile) -> Agent:
    return Agent(alice_profile)


@pytest.fixture
def bob(bob_profile: AgentProfile) -> Agent:
    return Agent(bob_profile)

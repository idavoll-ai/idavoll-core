from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeLLM(BaseChatModel):
    """Deterministic chat model used by the refactor bootstrap tests."""

    reply: str = "这是一次测试回复。"

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        del messages, kwargs
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.reply))]
        )

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()

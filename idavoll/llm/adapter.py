from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage


class LLMAdapter:
    """
    Thin wrapper around LangChain's BaseChatModel.

    Keeps LangChain types out of the rest of the framework — callers only
    deal with plain strings and BaseMessage lists.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def generate(self, messages: list[BaseMessage]) -> str:
        response: AIMessage = await self._model.ainvoke(messages)
        return str(response.content)

    @property
    def raw(self) -> BaseChatModel:
        """Escape hatch — returns the underlying LangChain model directly."""
        return self._model

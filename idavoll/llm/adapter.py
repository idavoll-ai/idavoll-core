from __future__ import annotations

from typing import Any

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

    async def generate(
        self,
        messages: list[BaseMessage],
        callbacks: list[Any] | None = None,
        run_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Invoke the underlying chat model.

        The optional *callbacks*, *run_name*, *metadata*, and *tags* are
        forwarded as a LangChain ``RunnableConfig`` so callers (e.g.
        ``LangSmithPlugin``) can attach tracing context without coupling
        the rest of the framework to any specific observability backend.
        """
        config: dict[str, Any] = {}
        if callbacks:
            config["callbacks"] = callbacks
        if run_name:
            config["run_name"] = run_name
        if metadata:
            config["metadata"] = metadata
        if tags:
            config["tags"] = tags
        response: AIMessage = await self._model.ainvoke(messages, config=config or {})
        return str(response.content)

    @property
    def raw(self) -> BaseChatModel:
        """Escape hatch — returns the underlying LangChain model directly."""
        return self._model

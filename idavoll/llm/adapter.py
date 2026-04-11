from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

if TYPE_CHECKING:
    from ..tools.registry import ToolSpec


class LLMAdapter:
    """
    Thin wrapper around LangChain's BaseChatModel.

    Keeps LangChain types out of the rest of the framework — callers only
    deal with plain strings and BaseMessage lists.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def _build_config(
        self,
        callbacks: list[Any] | None,
        run_name: str | None,
        metadata: dict[str, Any] | None,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if callbacks:
            config["callbacks"] = callbacks
        if run_name:
            config["run_name"] = run_name
        if metadata:
            config["metadata"] = metadata
        if tags:
            config["tags"] = tags
        return config

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
        config = self._build_config(callbacks, run_name, metadata, tags)
        response: AIMessage = await self._model.ainvoke(messages, config=config or {})
        return str(response.content)

    async def generate_with_tools(
        self,
        messages: list[BaseMessage],
        tools: "list[ToolSpec]",
        callbacks: list[Any] | None = None,
        run_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> AIMessage:
        """Invoke the model with tools bound; return the raw AIMessage.

        Unlike ``generate()``, this returns the full ``AIMessage`` so the
        caller can inspect ``tool_calls`` and drive the tool execution loop.

        Each ``ToolSpec`` is converted to the OpenAI function-calling schema
        that ``BaseChatModel.bind_tools()`` expects.  Specs with an empty
        ``parameters`` dict are given a minimal ``{"type": "object",
        "properties": {}}`` schema so the API call stays well-formed.
        """
        config = self._build_config(callbacks, run_name, metadata, tags)
        tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters
                    or {"type": "object", "properties": {}},
                },
            }
            for spec in tools
        ]
        model = self._model.bind_tools(tool_schemas) if tool_schemas else self._model
        return await model.ainvoke(messages, config=config or {})

    @property
    def raw(self) -> BaseChatModel:
        """Escape hatch — returns the underlying LangChain model directly."""
        return self._model

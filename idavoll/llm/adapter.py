from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

if TYPE_CHECKING:
    from ..tools.registry import ToolSpec


class LLMAdapter:
    """
    Thin wrapper around LangChain's BaseChatModel.

    Keeps LangChain types out of the rest of the framework — callers only
    deal with plain strings and BaseMessage lists.

    Public API:
        invoke   — single entry point; returns full AIMessage (inspect tool_calls here)
        generate — convenience wrapper around invoke; returns plain str
        astream  — streaming; yields str tokens; also accepts optional tools
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

    @staticmethod
    def _to_tool_schemas(tools: "list[ToolSpec]") -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters or {"type": "object", "properties": {}},
                },
            }
            for spec in tools
        ]

    async def invoke(
        self,
        messages: list[BaseMessage],
        *,
        tools: "list[ToolSpec] | None" = None,
        callbacks: list[Any] | None = None,
        run_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> AIMessage:
        """Invoke the model, returning the full AIMessage.

        Pass tools= to bind function-calling schemas.  Inspect result.tool_calls
        to drive a tool-execution loop; use generate() when you only need the
        plain text response.
        """
        config = self._build_config(callbacks, run_name, metadata, tags)
        model = (
            self._model.bind_tools(self._to_tool_schemas(tools))
            if tools
            else self._model
        )
        return await model.ainvoke(messages, config=config or {})

    async def generate(
        self,
        messages: list[BaseMessage],
        *,
        callbacks: list[Any] | None = None,
        run_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Invoke the model and return the response as a plain string.

        Use invoke() when you need the full AIMessage (e.g. to inspect tool_calls).
        """
        response = await self.invoke(
            messages,
            callbacks=callbacks,
            run_name=run_name,
            metadata=metadata,
            tags=tags,
        )
        return str(response.content)

    async def astream(
        self,
        messages: list[BaseMessage],
        *,
        tools: "list[ToolSpec] | None" = None,
        callbacks: list[Any] | None = None,
        run_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream the model response token by token, yielding plain strings.

        Pass tools= to bind function-calling schemas (tool-call chunks are
        skipped; only text content is yielded).
        """
        config = self._build_config(callbacks, run_name, metadata, tags)
        model = (
            self._model.bind_tools(self._to_tool_schemas(tools))
            if tools
            else self._model
        )
        async for chunk in model.astream(messages, config=config or {}):
            if chunk.content:
                yield str(chunk.content)

    @property
    def raw(self) -> BaseChatModel:
        """Escape hatch — returns the underlying LangChain model directly."""
        return self._model

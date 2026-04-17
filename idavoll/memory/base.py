from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools.registry import ToolSpec


class MemoryProvider(ABC):
    """Abstract base for all memory providers.

    Core contract
    -------------
    system_prompt_block()
        Returns a static markdown block compiled into the system prompt *once*
        at session start and frozen for the entire session.

    prefetch(query, context)
        Called at the start of each turn to retrieve memories relevant to the
        current message.  Returns a short string injected as
        ``<memory-context>`` before the user message.

    sync_turn(user_msg, assistant_msg)
        Called after each completed turn so providers can update internal
        state.  For the builtin provider this is a no-op; durable writes are
        driven by the memory tool calling MemoryStore directly.

    Optional hooks
    --------------
    on_memory_write(action, target, content)
        Notified after the builtin MemoryStore completes a write.  External
        providers implement this to mirror writes into their own backend
        (e.g. a vector DB or remote API).  The builtin provider is skipped
        — it is the source of the write.
    """

    @abstractmethod
    def system_prompt_block(self) -> str:
        """Return a static block for the frozen system prompt."""

    @abstractmethod
    async def prefetch(self, query: str, context: str = "") -> str:
        """Retrieve memories relevant to *query* for the current turn."""

    @abstractmethod
    async def sync_turn(self, user_msg: str, assistant_msg: str) -> None:
        """Record a completed turn (may be a no-op for simple providers)."""

    # ------------------------------------------------------------------
    # Optional hook — override in external providers
    # ------------------------------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
    ) -> None:
        """Called after a builtin MemoryStore write completes.

        *action* is ``"add"``, ``"replace"``, or ``"remove"``.
        *target* is ``"memory"`` or ``"user"``.
        *content* is the fact text (empty string for ``"remove"``).

        The default implementation is a no-op.  External providers override
        this to mirror writes into their own backend.
        """

    def get_tool_specs(self) -> list["ToolSpec"]:
        """Return tool specs contributed by this provider.

        External providers override this to expose additional tools to the
        agent (e.g. a ``fact_store`` search tool backed by a vector DB).
        The returned specs are merged into the agent's active tool list at
        session setup time and on every toolset unlock.

        The builtin provider returns an empty list — its tools (``memory``,
        ``session_search``) are registered through the normal ToolRegistry
        path and do not need to be declared here.
        """
        return []

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal


class MemoryProvider(ABC):
    """Abstract base for all memory providers.

    Core contract
    -------------
    system_prompt_block()
        Returns a static markdown block that is compiled into the system
        prompt *once* at session start and frozen for the entire session.

    prefetch(query, context)
        Called at the start of each turn to retrieve memories relevant to
        the current message.  Returns a short string injected as
        ``<memory-context>`` before the user message.

    sync_turn(user_msg, assistant_msg)
        Called after each completed turn so providers can update internal
        state.  For the builtin provider this is a no-op; actual durable
        writes are driven by the Self-Growth Engine.

    write_fact(content, target)  [optional]
        Persist a durable fact extracted by the Self-Growth Engine.
        Providers that do not support writes simply return False.
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

    def write_fact(
        self,
        content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Persist a durable fact.  Returns True if written, False if unsupported."""
        return False

    def replace_fact(
        self,
        old_text: str,
        new_content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Replace an existing fact.  Returns True if replaced, False if unsupported."""
        return False

    def remove_fact(
        self,
        old_text: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Remove an existing fact.  Returns True if removed, False if unsupported."""
        return False

    def read_facts(
        self,
        target: Literal["memory", "user"] = "memory",
    ) -> list[str]:
        """Return all current facts.  Returns empty list if unsupported."""
        return []

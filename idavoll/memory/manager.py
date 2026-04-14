from __future__ import annotations

from typing import Literal

from .base import MemoryProvider


class MemoryManager:
    """Orchestrates one or more MemoryProviders for a single Agent.

    The manager is the single point of contact for the session runtime:

    * ``system_prompt_block()``   — called once during prompt compilation.
    * ``prefetch(query, context)`` — called at the start of each turn.
    * ``sync_turn(user, assistant)`` — called after each completed turn.

    Providers are consulted in registration order.  Their outputs are
    concatenated, so the order also determines the final block layout.
    """

    def __init__(self) -> None:
        self._providers: list[MemoryProvider] = []

    # ------------------------------------------------------------------
    # Provider registration
    # ------------------------------------------------------------------

    def add_provider(self, provider: MemoryProvider) -> "MemoryManager":
        """Attach a provider.  Returns self for chaining."""
        self._providers.append(provider)
        return self

    @property
    def providers(self) -> list[MemoryProvider]:
        return list(self._providers)

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Collect static blocks from all providers and join them.

        Empty blocks are skipped so providers that have nothing to say
        don't add blank sections to the system prompt.
        """
        parts = [p.system_prompt_block() for p in self._providers]
        return "\n\n".join(part for part in parts if part.strip())

    async def prefetch(self, query: str, context: str = "") -> str:
        """Fetch relevant memories from all providers for the current turn."""
        results = []
        for provider in self._providers:
            chunk = await provider.prefetch(query, context)
            if chunk.strip():
                results.append(chunk)
        return "\n".join(results)

    async def sync_turn(self, user_msg: str, assistant_msg: str) -> None:
        """Notify all providers that a turn has completed."""
        for provider in self._providers:
            await provider.sync_turn(user_msg, assistant_msg)

    def write_fact(
        self,
        content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Write a durable fact to the first provider that supports it.

        Returns True if at least one provider accepted the write.
        """
        for provider in self._providers:
            if provider.write_fact(content, target):
                return True
        return False

    def replace_fact(
        self,
        old_text: str,
        new_content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Replace a fact in the first provider that finds a match."""
        for provider in self._providers:
            if provider.replace_fact(old_text, new_content, target):
                return True
        return False

    def remove_fact(
        self,
        old_text: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Remove a fact from the first provider that finds a match."""
        for provider in self._providers:
            if provider.remove_fact(old_text, target):
                return True
        return False

    def read_facts(
        self,
        target: Literal["memory", "user"] = "memory",
    ) -> list[str]:
        """Return the fact list from the first provider that has entries."""
        for provider in self._providers:
            facts = provider.read_facts(target)
            if facts:
                return facts
        return []

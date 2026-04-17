from __future__ import annotations

import logging

from .base import MemoryProvider

logger = logging.getLogger(__name__)


class MemoryManager:
    """Orchestrates one or more MemoryProviders for a single Agent.

    The manager is the single point of contact for the session runtime:

    * ``system_prompt_block()``      — called once during prompt compilation.
    * ``prefetch(query, context)``   — called at the start of each turn.
    * ``sync_turn(user, assistant)`` — called after each completed turn.
    * ``on_memory_write(...)``       — broadcast after the tool layer writes to
                                       MemoryStore, so external providers can
                                       mirror the write into their own backend.

    The manager does NOT expose fact CRUD.  All writes go through the
    ``memory`` tool → MemoryStore directly.  The manager's only role after a
    write is to fan out the event to all registered providers.

    Providers are consulted in registration order.
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
    # Session interface
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

    # ------------------------------------------------------------------
    # Provider tools
    # ------------------------------------------------------------------

    def get_tool_specs(self) -> list:
        """Return the deduplicated union of tool specs from all providers.

        Providers are visited in registration order.  If two providers
        declare a tool with the same name the first one wins (no silent
        override).  The builtin provider returns an empty list by default,
        so its tools are never affected.
        """
        seen: set[str] = set()
        specs: list = []
        for provider in self._providers:
            for spec in provider.get_tool_specs():
                if spec.name not in seen:
                    seen.add(spec.name)
                    specs.append(spec)
        return specs

    # ------------------------------------------------------------------
    # Write broadcast
    # ------------------------------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
    ) -> None:
        """Broadcast a completed MemoryStore write to all registered providers.

        Called by the ``memory`` tool after a successful write to MemoryStore.
        Each provider's ``on_memory_write`` hook is called so external providers
        can mirror the write (e.g. insert into a vector DB or remote API).
        The builtin provider's hook is a no-op by default, so it is safe to
        include in the fan-out.

        Exceptions from individual providers are logged and swallowed so that
        one failing backend does not surface to the user.
        """
        for provider in self._providers:
            try:
                provider.on_memory_write(action, target, content)
            except Exception:
                logger.debug(
                    "on_memory_write failed for provider %r",
                    type(provider).__name__,
                    exc_info=True,
                )

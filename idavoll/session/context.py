from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class SessionSearchLike(Protocol):
    async def search(self, query: str, context: str = "") -> str: ...


@dataclass(slots=True)
class SessionServices:
    """Session-scoped service container.

    Services that need the current session as the coordination boundary live
    here instead of being attached directly to ``Agent``.
    """

    session_search_factory: Callable[[str], SessionSearchLike | None] | None = None
    _session_search_cache: dict[str, SessionSearchLike] = field(default_factory=dict)

    def session_search_for(self, agent_id: str) -> SessionSearchLike | None:
        if agent_id in self._session_search_cache:
            return self._session_search_cache[agent_id]
        if self.session_search_factory is None:
            return None
        service = self.session_search_factory(agent_id)
        if service is not None:
            self._session_search_cache[agent_id] = service
        return service


def estimate_tokens(text: str) -> int:
    """Very rough token estimate for prompt budgeting."""

    if not text:
        return 0
    return max(1, len(text) // 4)

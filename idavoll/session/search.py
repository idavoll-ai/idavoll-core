"""Session Search — cross-session experience recall interface.

The file-based implementation has been replaced by ``SQLiteSessionSearch``
in the Vingolf persistence layer.  This module retains only the abstract
interface so that ``agent.session_search`` is typed consistently regardless
of the backing store.

Anything assigned to ``agent.session_search`` must implement::

    async def search(self, query: str, context: str = "") -> str: ...
"""
from __future__ import annotations


class SessionSearch:
    """No-op fallback used when no backing store has been configured.

    In production Vingolf always replaces this with ``SQLiteSessionSearch``.
    Tests that do not need cross-session recall can leave the no-op in place.
    """

    async def search(self, query: str, context: str = "") -> str:
        return ""

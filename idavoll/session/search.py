"""Session Search — cross-session experience recall interface.

The file-based implementation has been replaced by ``SQLiteSessionSearch``
in the Vingolf persistence layer.  This module retains only the no-op
interface so that session-scoped service resolvers can return a consistent
object regardless of the backing store.

Any session search implementation must expose::

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

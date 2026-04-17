from __future__ import annotations

import re

from ..session.context import estimate_tokens
from .base import MemoryProvider
from .store import MemoryStore


class BuiltinMemoryProvider(MemoryProvider):
    """Adapter that exposes a MemoryStore as a MemoryProvider.

    Responsibilities
    ----------------
    * ``system_prompt_block()``  — frozen snapshot injected at session start.
    * ``prefetch(query)``        — keyword-based recall for the current turn.
    * ``sync_turn(...)``         — no-op; durable writes go through the
                                   ``memory`` tool → MemoryStore directly.

    This provider does NOT perform CRUD operations.  All fact writes happen
    in the tool layer (tools/builtin/memory.py) via the MemoryStore, after
    which MemoryManager broadcasts an ``on_memory_write`` event to any
    external providers that need to mirror the write.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        system_block_token_budget: int = 400,
        prefetch_token_budget: int = 200,
    ) -> None:
        self._store = store
        self._system_budget = system_block_token_budget
        self._prefetch_budget = prefetch_token_budget

    # ------------------------------------------------------------------
    # MemoryProvider interface
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Return the frozen block combining MEMORY.md and USER.md.

        Reads from the store's frozen snapshot (captured by
        ``MemoryStore.load_snapshot()`` at session start).  Mid-session
        writes do not affect this output, keeping the system prompt stable
        and preserving the LLM's prefix cache.

        Layout::

            [memory guidance header]
            ── MEMORY（你的笔记）─── n 条
            - fact ...
            ── USER PROFILE（用户档案）─── n 条
            - fact ...
        """
        memory_block = self._store.format_for_system_prompt("memory")
        user_block = self._store.format_for_system_prompt("user")

        if not memory_block and not user_block:
            return ""

        parts: list[str] = [
            "【记忆管理规则】\n"
            "以下是你在历史 session 中积累的持久记忆，已在本 session 开始时冻结注入。\n"
            "- 发现记忆有误或过时 → 调用 memory(action=\"replace\") 或 memory(action=\"remove\")\n"
            "- 本次对话中习得值得保留的新事实 → 调用 memory(action=\"add\")\n"
            "- 不确定当前条目内容 → 调用 memory(action=\"read\") 查看实时状态",
        ]
        if memory_block:
            parts.append(memory_block)
        if user_block:
            parts.append(user_block)

        return self._truncate("\n\n".join(parts), self._system_budget)

    async def prefetch(self, query: str, context: str = "") -> str:
        """Return facts relevant to *query* using keyword scoring.

        Strategy (MVP — no embeddings):
        1. Tokenise the query into words.
        2. Score each fact by how many query words it contains.
        3. Return the top-scoring facts, up to the prefetch budget.
        4. If nothing matches, return an empty string.
        """
        memory_facts = self._store.read_facts("memory")
        user_facts = self._store.read_facts("user")
        all_facts = [("memory", f) for f in memory_facts] + [
            ("user", f) for f in user_facts
        ]

        if not all_facts:
            return ""

        query_tokens = re.findall(r"\w+", (query + " " + context).lower())
        if not query_tokens:
            return ""

        scored: list[tuple[int, str, str]] = []
        for source, fact in all_facts:
            fact_lower = fact.lower()
            score = sum(1 for token in query_tokens if token in fact_lower)
            if score > 0:
                scored.append((score, source, fact))

        if not scored:
            return ""

        scored.sort(key=lambda t: t[0], reverse=True)

        lines: list[str] = []
        used_tokens = 0
        for _, source, fact in scored:
            label = "[memory]" if source == "memory" else "[user]"
            line = f"- {label} {fact}"
            tokens = estimate_tokens(line)
            if used_tokens + tokens > self._prefetch_budget:
                break
            lines.append(line)
            used_tokens += tokens

        if not lines:
            return ""

        return "<memory-context>\n" + "\n".join(lines) + "\n</memory-context>"

    async def sync_turn(self, user_msg: str, assistant_msg: str) -> None:
        # Durable writes are the tool layer's responsibility.
        # This provider only surfaces data for prompts.
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        if estimate_tokens(text) <= token_budget:
            return text
        char_limit = token_budget * 4
        return text[:char_limit] + "\n<!-- [memory truncated] -->"

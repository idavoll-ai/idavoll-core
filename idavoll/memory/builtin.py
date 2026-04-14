from __future__ import annotations

import re
from typing import Literal

from ..agent.workspace import ProfileWorkspace
from ..session.context import estimate_tokens
from .base import MemoryProvider

# ---------------------------------------------------------------------------
# Hard-constraint helpers
# ---------------------------------------------------------------------------

# Patterns that indicate a prompt-injection attempt.
_INJECTION_PATTERNS = re.compile(
    r"ignore\s+(all\s+)?previous\s+instructions?"
    r"|system\s*prompt"
    r"|<\s*system\s*>"
    r"|you\s+are\s+now\s+a"
    r"|\[\s*system\s*\]",
    re.IGNORECASE,
)

_MAX_FACT_LENGTH = 500  # characters; longer facts are rejected


def _validate_fact(content: str) -> str:
    """Return cleaned content or raise ValueError on hard-constraint violations."""
    content = content.strip()
    if not content:
        raise ValueError("Memory fact must not be empty.")
    if len(content) > _MAX_FACT_LENGTH:
        raise ValueError(
            f"Memory fact exceeds maximum length ({len(content)} > {_MAX_FACT_LENGTH})."
        )
    if _INJECTION_PATTERNS.search(content):
        raise ValueError("Memory fact contains a disallowed injection pattern.")
    return content


# ---------------------------------------------------------------------------
# Fact parsing / serialisation
# ---------------------------------------------------------------------------

def _parse_facts(text: str) -> list[str]:
    """Extract bullet-point facts from a MEMORY.md / USER.md string."""
    facts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 2:
            facts.append(stripped[2:].strip())
    return facts


def _append_fact(current_text: str, fact: str) -> str:
    """Append a new bullet fact to the end of a markdown file string."""
    if not current_text.endswith("\n"):
        current_text += "\n"
    return current_text + f"- {fact}\n"


def _rebuild(current_text: str, facts: list[str]) -> str:
    """Reconstruct the file text from a mutated facts list.

    Preserves the header section (everything before the first bullet) so
    comments and titles are not lost on replace/remove operations.
    """
    lines = current_text.splitlines(keepends=True)
    header: list[str] = []
    for line in lines:
        if line.lstrip().startswith("- "):
            break
        header.append(line)
    header_text = "".join(header)
    if not header_text.endswith("\n"):
        header_text += "\n"
    body = "".join(f"- {f}\n" for f in facts)
    return header_text + body


# ---------------------------------------------------------------------------
# BuiltinMemoryProvider
# ---------------------------------------------------------------------------

class BuiltinMemoryProvider(MemoryProvider):
    """Reads and writes MEMORY.md + USER.md inside a Profile Workspace.

    Responsibilities
    ----------------
    * ``system_prompt_block()``  — frozen snapshot injected at session start.
    * ``prefetch(query)``        — keyword-based recall for the current turn.
    * ``sync_turn(...)``         — no-op; durable writes go through
                                   ``write_fact()`` (called by Self-Growth Engine).
    * ``write_fact(content, target)`` — append a validated durable fact.
    """

    TARGET_MEMORY: Literal["memory"] = "memory"
    TARGET_USER: Literal["user"] = "user"

    def __init__(
        self,
        workspace: ProfileWorkspace,
        *,
        system_block_token_budget: int = 400,
        prefetch_token_budget: int = 200,
    ) -> None:
        self._ws = workspace
        self._system_budget = system_block_token_budget
        self._prefetch_budget = prefetch_token_budget

    # ------------------------------------------------------------------
    # MemoryProvider interface
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        """Return a frozen block combining MEMORY.md and USER.md with usage guidance.

        Layout injected into the static system prompt (frozen for the session):

            [memory guidance header]
            ── MEMORY ──────────────── n/N chars
            - fact ...
            ── USER PROFILE ─────────── n/N chars
            - fact ...

        The guidance header tells the LLM what these sections are and how to
        actively curate them via the ``memory`` tool.  Both sections are only
        rendered when they contain at least one fact.
        """
        memory_facts = _parse_facts(self._ws.read_memory())
        user_facts = _parse_facts(self._ws.read_user())

        if not memory_facts and not user_facts:
            return ""

        parts: list[str] = [
            "【记忆管理规则】\n"
            "以下是你在历史 session 中积累的持久记忆，已在本 session 开始时冻结注入。\n"
            "- 发现记忆有误或过时 → 调用 memory(action=\"replace\") 或 memory(action=\"remove\")\n"
            "- 本次对话中习得值得保留的新事实 → 调用 memory(action=\"add\")\n"
            "- 不确定当前条目内容 → 调用 memory(action=\"read\") 查看实时状态",
        ]

        if memory_facts:
            parts.append(self._render_section("MEMORY（你的笔记）", memory_facts))
        if user_facts:
            parts.append(self._render_section("USER PROFILE（用户档案）", user_facts))

        block = "\n\n".join(parts)
        return self._truncate(block, self._system_budget)

    async def prefetch(self, query: str, context: str = "") -> str:
        """Return MEMORY.md lines relevant to *query*.

        Strategy (MVP — no embeddings):
        1. Tokenise the query into words.
        2. Score each fact by how many query words it contains.
        3. Return the top-scoring facts, up to the prefetch budget.
        4. If nothing matches, return an empty string.
        """
        memory_facts = _parse_facts(self._ws.read_memory())
        user_facts = _parse_facts(self._ws.read_user())
        all_facts = [("memory", f) for f in memory_facts] + [
            ("user", f) for f in user_facts
        ]

        if not all_facts:
            return ""

        # Split on whitespace/punctuation for multi-lingual support.
        # For CJK text, individual characters/words won't be space-separated,
        # so we use substring containment rather than token-set intersection.
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
        # Durable writes are the Self-Growth Engine's responsibility.
        # This provider only handles explicit write_fact() calls.
        pass

    # ------------------------------------------------------------------
    # Write interface (called by Self-Growth Engine / tools)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Write / edit interface (called by Self-Growth Engine / tools)
    # ------------------------------------------------------------------

    def write_fact(
        self,
        content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Append a validated durable fact to MEMORY.md or USER.md.

        Hard constraints enforced here:
        * Non-empty content
        * Length ≤ 500 characters
        * No injection patterns
        * No exact duplicate of an existing fact

        Returns True if the fact was written, False if it was a duplicate.
        Raises ValueError on hard-constraint violations.
        """
        content = _validate_fact(content)

        if target == "memory":
            current = self._ws.read_memory()
        else:
            current = self._ws.read_user()

        existing = _parse_facts(current)
        if content in existing:
            return False  # exact duplicate — skip silently

        updated = _append_fact(current, content)

        if target == "memory":
            self._ws.write_memory(updated)
        else:
            self._ws.write_user(updated)
        return True

    def replace_fact(
        self,
        old_text: str,
        new_content: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Find the entry containing *old_text* and replace it with *new_content*.

        Uses substring matching so callers pass a short unique fragment rather
        than the full entry text.  Returns True if replaced, False if no match.
        Raises ValueError when *new_content* fails validation or multiple
        distinct entries match (ambiguous — caller must be more specific).
        """
        old_text = old_text.strip()
        new_content = _validate_fact(new_content)

        current = self._ws.read_memory() if target == "memory" else self._ws.read_user()
        facts = _parse_facts(current)

        matches = [i for i, f in enumerate(facts) if old_text in f]
        if not matches:
            return False
        if len(matches) > 1 and len({facts[i] for i in matches}) > 1:
            raise ValueError(
                f"Ambiguous: {len(matches)} distinct entries contain {old_text!r}. "
                "Provide a longer substring to uniquely identify the target."
            )

        facts[matches[0]] = new_content
        updated = _rebuild(current, facts)
        if target == "memory":
            self._ws.write_memory(updated)
        else:
            self._ws.write_user(updated)
        return True

    def remove_fact(
        self,
        old_text: str,
        target: Literal["memory", "user"] = "memory",
    ) -> bool:
        """Remove the entry containing *old_text* (substring match).

        Returns True if removed, False if no match.
        Raises ValueError when multiple distinct entries match.
        """
        old_text = old_text.strip()

        current = self._ws.read_memory() if target == "memory" else self._ws.read_user()
        facts = _parse_facts(current)

        matches = [i for i, f in enumerate(facts) if old_text in f]
        if not matches:
            return False
        if len(matches) > 1 and len({facts[i] for i in matches}) > 1:
            raise ValueError(
                f"Ambiguous: {len(matches)} distinct entries contain {old_text!r}. "
                "Provide a longer substring to uniquely identify the target."
            )

        facts.pop(matches[0])
        updated = _rebuild(current, facts)
        if target == "memory":
            self._ws.write_memory(updated)
        else:
            self._ws.write_user(updated)
        return True

    def read_facts(
        self,
        target: Literal["memory", "user"] = "memory",
    ) -> list[str]:
        """Return the current list of facts for *target*."""
        current = self._ws.read_memory() if target == "memory" else self._ws.read_user()
        return _parse_facts(current)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_section(title: str, facts: list[str]) -> str:
        """Render one memory section with a header showing entry count.

        Example output::

            ── MEMORY（你的笔记）─────────── 3 条
            - fact one
            - fact two
            - fact three
        """
        sep = "─" * max(0, 40 - len(title))
        header = f"── {title} {sep} {len(facts)} 条"
        body = "\n".join(f"- {f}" for f in facts)
        return f"{header}\n{body}"

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        if estimate_tokens(text) <= token_budget:
            return text
        # Truncate by characters (rough approximation: 1 token ≈ 4 chars).
        char_limit = token_budget * 4
        return text[:char_limit] + "\n<!-- [memory truncated] -->"

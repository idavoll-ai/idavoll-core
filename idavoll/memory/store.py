from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Hard-constraint helpers
# ---------------------------------------------------------------------------

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
# Serialisation helpers
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
# MemoryStore
# ---------------------------------------------------------------------------

Target = Literal["memory", "user"]

_SECTION_TITLES: dict[str, str] = {
    "memory": "MEMORY（你的笔记）",
    "user": "USER PROFILE（用户档案）",
}


def _render_section(title: str, facts: list[str]) -> str:
    """Render one memory section with a header showing entry count.

    Example output::

        ── MEMORY（你的笔记）─────── 3 条
        - fact one
        - fact two
        - fact three
    """
    sep = "─" * max(0, 40 - len(title))
    header = f"── {title} {sep} {len(facts)} 条"
    body = "\n".join(f"- {f}" for f in facts)
    return f"{header}\n{body}"


class MemoryStore:
    """Durable fact store backed by two markdown files (MEMORY.md and USER.md).

    This class owns all file I/O, fact serialisation, and the frozen system
    prompt snapshot.  It is intentionally decoupled from agent definitions
    and session logic — callers supply the concrete file paths at
    construction time.

    Responsibilities
    ----------------
    * Read / write raw file content for ``memory`` and ``user`` targets.
    * Parse, validate, and mutate bullet-point fact lists.
    * Enforce hard constraints (max length, injection patterns, duplicates).
    * Maintain a frozen snapshot for stable system prompt injection.

    Snapshot contract
    -----------------
    ``load_snapshot()`` must be called once at session start (after the
    agent's workspace is ready).  It captures the current file state and
    freezes it in ``_snapshot``.  Subsequent writes via ``add_fact`` /
    ``replace_fact`` / ``remove_fact`` update the live files but do NOT
    update the snapshot, so the system prompt stays byte-for-byte identical
    throughout the session — preserving the LLM's prefix cache.

    ``format_for_system_prompt(target)`` always returns the frozen snapshot,
    never the live file state.
    """

    TARGET_MEMORY: Literal["memory"] = "memory"
    TARGET_USER: Literal["user"] = "user"

    def __init__(self, memory_path: Path, user_path: Path) -> None:
        self._paths: dict[str, Path] = {
            self.TARGET_MEMORY: memory_path,
            self.TARGET_USER: user_path,
        }
        # Frozen per-target rendered blocks.  Empty until load_snapshot().
        self._snapshot: dict[str, str] = {
            self.TARGET_MEMORY: "",
            self.TARGET_USER: "",
        }

    # ------------------------------------------------------------------
    # Snapshot (frozen at session start)
    # ------------------------------------------------------------------

    def load_snapshot(self) -> None:
        """Read both files and freeze their rendered content as the snapshot.

        Must be called once at session start, before the first system prompt
        is compiled.  Safe to call again (e.g. after a session reload) — the
        snapshot is simply overwritten.
        """
        for target in (self.TARGET_MEMORY, self.TARGET_USER):
            facts = self.read_facts(target)
            title = _SECTION_TITLES[target]
            self._snapshot[target] = _render_section(title, facts) if facts else ""

    def format_for_system_prompt(self, target: Target = "memory") -> str:
        """Return the frozen rendered block for *target*.

        Always returns the snapshot captured by ``load_snapshot()``, never
        the live file state.  Returns an empty string when the snapshot is
        empty or ``load_snapshot()`` has not been called yet.
        """
        return self._snapshot.get(target, "")

    # ------------------------------------------------------------------
    # Raw I/O
    # ------------------------------------------------------------------

    def read_raw(self, target: Target = "memory") -> str:
        """Return the raw file content for *target*."""
        path = self._paths[target]
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_raw(self, target: Target, content: str) -> None:
        """Overwrite the file for *target* with *content*."""
        path = self._paths[target]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Fact CRUD
    # ------------------------------------------------------------------

    def read_facts(self, target: Target = "memory") -> list[str]:
        """Return the current list of facts for *target*."""
        return _parse_facts(self.read_raw(target))

    def add_fact(
        self,
        content: str,
        target: Target = "memory",
    ) -> bool:
        """Append a validated durable fact to the target file.

        Hard constraints enforced:
        * Non-empty content
        * Length ≤ 500 characters
        * No injection patterns
        * No exact duplicate of an existing fact

        Returns True if written, False if it was an exact duplicate.
        Raises ValueError on constraint violations.
        """
        content = _validate_fact(content)
        current = self.read_raw(target)
        existing = _parse_facts(current)
        if content in existing:
            return False
        self.write_raw(target, _append_fact(current, content))
        return True

    def replace_fact(
        self,
        old_text: str,
        new_content: str,
        target: Target = "memory",
    ) -> bool:
        """Find the entry containing *old_text* and replace it with *new_content*.

        Uses substring matching so callers pass a short unique fragment rather
        than the full entry text.  Returns True if replaced, False if no match.
        Raises ValueError when *new_content* fails validation or multiple
        distinct entries match (ambiguous).
        """
        old_text = old_text.strip()
        new_content = _validate_fact(new_content)

        current = self.read_raw(target)
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
        self.write_raw(target, _rebuild(current, facts))
        return True

    def remove_fact(
        self,
        old_text: str,
        target: Target = "memory",
    ) -> bool:
        """Remove the entry containing *old_text* (substring match).

        Returns True if removed, False if no match.
        Raises ValueError when multiple distinct entries match.
        """
        old_text = old_text.strip()

        current = self.read_raw(target)
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
        self.write_raw(target, _rebuild(current, facts))
        return True

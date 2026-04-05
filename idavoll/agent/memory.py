from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


# ── Memory Plan (记忆规划) ─────────────────────────────────────────────────────
# Defined per-agent as part of the profile. Tells the consolidator *what kinds
# of things* this agent cares about remembering. Different personas naturally
# produce different plans — a scientist remembers hypotheses, a debater
# remembers stance shifts.

class MemoryCategory(BaseModel):
    """One slot in the agent's memory plan."""

    name: str = Field(description="分类标识，用作 YAML key，例如 core_beliefs")
    description: str = Field(
        description="告诉 LLM 从对话中提取什么，例如 '我在讨论中形成的核心观点'",
    )
    max_entries: int = Field(
        default=20,
        description="该分类最多保留多少条记忆，超出后丢弃最旧的",
    )


class MemoryPlan(BaseModel):
    """
    Per-agent declaration of what to remember.

    This is the 'planning' layer — the agent (or its creator) defines the schema
    of its long-term memory. The consolidator uses these descriptions as
    extraction instructions after each session.

    Example::

        MemoryPlan(categories=[
            MemoryCategory(
                name="core_beliefs",
                description="经过讨论后我形成或加深的核心观点",
                max_entries=10,
            ),
            MemoryCategory(
                name="notable_ideas",
                description="遇到的令我印象深刻的思想或论点",
                max_entries=20,
            ),
        ])
    """

    categories: list[MemoryCategory] = Field(default_factory=list)

    def get_category(self, name: str) -> MemoryCategory | None:
        for c in self.categories:
            if c.name == name:
                return c
        return None


# ── Memory Entries (记忆条目) ──────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    """A single remembered fact or observation."""

    content: str = Field(description="记忆内容，一句话描述")
    formed_at: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="记忆形成日期，ISO 格式 YYYY-MM-DD",
    )
    session_id: str | None = Field(
        default=None,
        description="来源 session，方便溯源",
    )


class AgentMemory(BaseModel):
    """
    All accumulated memories for one agent, organized by category.

    Keyed by MemoryCategory.name. The consolidator writes here after each
    session; the prompt builder reads here to inject context.
    """

    entries: dict[str, list[MemoryEntry]] = Field(default_factory=dict)

    def get(self, category: str) -> list[MemoryEntry]:
        return self.entries.get(category, [])

    def add(self, category: str, entry: MemoryEntry, max_entries: int) -> None:
        bucket = self.entries.setdefault(category, [])
        bucket.append(entry)
        if len(bucket) > max_entries:
            # Keep the most recent entries
            self.entries[category] = bucket[-max_entries:]

    def to_context_text(self, plan: MemoryPlan, max_tokens: int) -> str:
        """
        Render memories as a compact text block for prompt injection.

        Iterates categories in plan order, newest-first within each category,
        and stops when the estimated token budget is exhausted.
        """
        if not self.entries or not plan.categories:
            return ""

        lines: list[str] = ["## 长期记忆"]
        token_used = 5  # header estimate

        for cat in plan.categories:
            bucket = self.get(cat.name)
            if not bucket:
                continue

            header = f"\n### {cat.name}"
            token_used += len(header) // 3
            if token_used >= max_tokens:
                break
            lines.append(header)

            for entry in reversed(bucket):  # newest first
                line = f"- {entry.content}（{entry.formed_at}）"
                cost = len(line) // 3
                if token_used + cost > max_tokens:
                    break
                lines.append(line)
                token_used += cost

        return "\n".join(lines) if len(lines) > 1 else ""

    def model_dump(self, **kwargs: Any) -> dict:  # type: ignore[override]
        """Serialize entries as plain dicts for YAML output."""
        return {
            "entries": {
                cat: [e.model_dump() for e in items]
                for cat, items in self.entries.items()
            }
        }

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "AgentMemory":  # type: ignore[override]
        if isinstance(obj, dict) and "entries" in obj:
            raw = obj["entries"]
            entries = {
                cat: [MemoryEntry.model_validate(e) for e in items]
                for cat, items in raw.items()
            }
            return cls(entries=entries)
        return cls()

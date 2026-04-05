from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .memory import AgentMemory, MemoryCategory, MemoryEntry

if TYPE_CHECKING:
    from ..session.session import Session
    from .registry import Agent


# ── Structured output for one category's extraction ───────────────────────────

class _ExtractedEntries(BaseModel):
    """What the LLM returns for a single memory category."""

    entries: list[str] = Field(
        default_factory=list,
        description="Extracted memory items. Each item is one concise sentence.",
    )


_SYSTEM_PROMPT = """\
You are a memory extractor for an AI agent.

You will receive:
1. A description of the agent's persona (who they are).
2. A memory category with instructions on what to extract.
3. The full transcript of a conversation the agent participated in.

Your job: extract 0-3 concise, self-contained sentences that belong in that memory \
category, written from the agent's first-person perspective.

Rules:
- Only extract things that are meaningfully new or insightful — skip small talk.
- Each sentence must stand alone without needing the conversation for context.
- If nothing qualifies, return an empty list.
- Write in the same language as the agent's voice (check the persona).
"""

_HUMAN_TEMPLATE = """\
Agent persona:
{persona}

Memory category to extract:
Name: {category_name}
Instructions: {category_description}

Conversation transcript:
{transcript}
"""


class MemoryConsolidator:
    """
    Reviews a completed session and extracts new memories per agent's memory_plan.

    Called once per agent after a session closes. For each MemoryCategory in the
    agent's plan, it runs one LLM call to extract relevant entries, then writes
    them into agent.memory.

    Usage (typically wired via app.py hook)::

        consolidator = MemoryConsolidator(llm)
        await consolidator.consolidate(agent, session)
    """

    def __init__(self, llm: BaseChatModel) -> None:
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("human", _HUMAN_TEMPLATE)]
        )
        self._chain = prompt | llm.with_structured_output(
            _ExtractedEntries, method="function_calling"
        )

    async def consolidate(self, agent: "Agent", session: "Session") -> AgentMemory:
        """
        Extract memories from session and merge into agent.memory.

        Returns the (mutated) agent.memory for convenience.
        """
        plan = agent.profile.memory_plan
        if not plan.categories:
            return agent.memory

        transcript = _build_transcript(session)
        if not transcript.strip():
            return agent.memory

        persona = _build_persona_summary(agent)

        for category in plan.categories:
            new_entries = await self._extract_category(persona, category, transcript)
            for content in new_entries:
                agent.memory.add(
                    category.name,
                    MemoryEntry(content=content, session_id=session.id),
                    category.max_entries,
                )

        return agent.memory

    async def _extract_category(
        self,
        persona: str,
        category: MemoryCategory,
        transcript: str,
    ) -> list[str]:
        result: _ExtractedEntries | None = await self._chain.ainvoke(
            {
                "persona": persona,
                "category_name": category.name,
                "category_description": category.description,
                "transcript": transcript,
            }
        )
        if result is None:
            return []
        return result.entries


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_transcript(session: "Session") -> str:
    return "\n".join(
        f"[{msg.agent_name}]: {msg.content}" for msg in session.messages
    )


def _build_persona_summary(agent: "Agent") -> str:
    identity = agent.profile.identity
    voice = agent.profile.voice
    parts = [f"Name: {agent.profile.name}"]
    if identity.role:
        parts.append(f"Role: {identity.role}")
    if identity.goal:
        parts.append(f"Goal: {identity.goal}")
    if voice.tone:
        parts.append(f"Tone: {voice.tone}")
    if voice.language:
        parts.append(f"Language: {voice.language}")
    return "\n".join(parts)

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from ..session.context import _estimate_tokens

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..session.session import Session

# ── Section templates ──────────────────────────────────────────────────────────

# Section 1: System Instruction — Identity layer compiled result
_IDENTITY_TMPL = "你是{name}。{role}{backstory}你的目标是{goal}"

# Section 2: Voice Rules — tone + quirks constraint + few-shot examples
_VOICE_RULES_TMPL = """\

## 表达风格
- 语气：{tone}
- 说话习惯：{quirks}
- 语言：{language}"""

_VOICE_EXAMPLES_TMPL = """\

## 示例
{examples}"""

# Section 5: Post Instructions — reminder closest to generation
_POST_INSTRUCTIONS = """\

---
保持以上人设参与对话。风格自然，直接切入话题，无需自我介绍。"""

# Kick-off prompt for the very first turn (no history yet)
_KICKOFF = "请分享你的开场观点。"

# Memory budget: fraction of available tokens reserved for long-term memory
_MEMORY_BUDGET_RATIO = 0.2


class PromptBuilder:
    """
    Assembles the full message list sent to the LLM for one agent turn.

    Context window layout (top → bottom):
      1. System Instruction   Identity layer → "你是{name}。{role}…{goal}"
      2. Voice Rules          tone + quirks → constraint text + few-shot examples
      3. Long-term Memory     agent.memory rendered from memory_plan categories
                              (capped at 20% of available budget)
      4. Scene Context        product/plugin layer, injected via beforeGenerate hook
                              (from session.metadata["scene_context"], capped by budget)
      5. Conversation History newest-first fill until token budget exhausted
      6. Post Instructions    persona reminder, placed just before generation

    Sections 1-4 + 6 are packed into a single SystemMessage (model compatibility).
    Section 5 is the message list that follows.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def build(
        self,
        agent: "Agent",
        session: "Session",
        scene_context: str = "",
    ) -> list[BaseMessage]:
        """
        Build the full message list for one agent turn.

        Args:
            agent:         The agent that will speak next.
            session:       Current session (provides history + budget source).
            scene_context: Plugin-injected context for this specific turn
                           (e.g. topic description, debate rules). Comes from
                           session.metadata["scene_context"] set by the
                           agent.before_generate hook.
        """
        budget = agent.profile.budget
        memory_budget = int(budget.available * _MEMORY_BUDGET_RATIO)

        system_text, fixed_tokens = self._build_system(
            agent, scene_context, budget.scene_context_max, memory_budget
        )

        history_budget = budget.available - fixed_tokens
        history = session.context.get_for_agent_with_budget(agent.id, max(0, history_budget))

        messages: list[BaseMessage] = [SystemMessage(content=system_text)]
        messages.extend(history)

        if not history:
            messages.append(HumanMessage(content=_KICKOFF))

        return messages

    # ── Section builders ───────────────────────────────────────────────────────

    def _build_system(
        self,
        agent: "Agent",
        scene_context: str,
        scene_max_tokens: int,
        memory_max_tokens: int,
    ) -> tuple[str, int]:
        """
        Assemble sections 1 + 2 + 3 + 4 + 6 into one system string.

        Returns (system_text, estimated_token_count).
        """
        profile = agent.profile

        # Section 1: Identity
        identity = profile.identity
        backstory_part = f"{identity.backstory}" if identity.backstory else ""
        if backstory_part and not backstory_part.endswith("。"):
            backstory_part += "。"
        s1 = _IDENTITY_TMPL.format(
            name=profile.name,
            role=identity.role,
            backstory=backstory_part,
            goal=identity.goal,
        )

        # Section 2: Voice Rules
        voice = profile.voice
        quirks_text = "、".join(voice.quirks) if voice.quirks else "无特殊习惯"
        s2 = _VOICE_RULES_TMPL.format(
            tone=voice.tone,
            quirks=quirks_text,
            language=voice.language,
        )

        # Section 2 continued: few-shot examples (max 3)
        s2_examples = ""
        if voice.example_messages:
            examples = voice.example_messages[:3]
            lines = []
            for ex in examples:
                lines.append(f"[用户]: {ex.input}")
                lines.append(f"[{profile.name}]: {ex.output}")
            s2_examples = _VOICE_EXAMPLES_TMPL.format(examples="\n".join(lines))

        # Section 3: Long-term Memory (from agent.memory, budget-capped)
        s3 = ""
        if memory_max_tokens > 0:
            memory_text = agent.memory.to_context_text(profile.memory_plan, memory_max_tokens)
            if memory_text:
                s3 = f"\n\n{memory_text}"

        # Section 4: Scene Context (plugin-injected, capped)
        s4 = ""
        if scene_context.strip():
            trimmed = _trim_to_tokens(scene_context, scene_max_tokens)
            s4 = f"\n\n---\n{trimmed}"

        # Section 6: Post Instructions
        s6 = _POST_INSTRUCTIONS

        system_text = s1 + s2 + s2_examples + s3 + s4 + s6
        return system_text, _estimate_tokens(system_text)


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text so its estimated token count fits within max_tokens."""
    max_chars = max_tokens * 3  # inverse of _estimate_tokens
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"

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


class PromptBuilder:
    """
    Assembles the full message list sent to the LLM for one agent turn.

    Context window layout (top → bottom) — exactly 5 sections per mvp_design:
      1. System Instruction   Identity layer → "你是{name}。{role}…{goal}"
      2. Voice Rules          tone + quirks → constraint text + few-shot examples
      3. Scene Context        All product/plugin-injected context, split into two
                              sub-parts assembled by the caller before build():
                                a. memory_context — long-term memory rendered by
                                   the framework's beforeGenerate hook
                                   (capped by budget.memory_context_max)
                                b. scene_context  — topic/debate context injected
                                   by product plugins via session.metadata
                                   (capped by budget.scene_context_max)
      4. Conversation History newest-first fill until token budget exhausted
      5. Post Instructions    persona reminder, placed just before generation

    Sections 1-3 + 5 are packed into a single SystemMessage (model compatibility).
    Section 4 is the message list that follows.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def build(
        self,
        agent: "Agent",
        session: "Session",
        scene_context: str = "",
        memory_context: str = "",
    ) -> list[BaseMessage]:
        """
        Build the full message list for one agent turn.

        Args:
            agent:          The agent that will speak next.
            session:        Current session (provides history + budget source).
            scene_context:  Plugin-injected context (topic description, rules, etc.).
                            Set by product plugins via session.metadata["scene_context"]
                            in the agent.before_generate hook.
            memory_context: Long-term memory text rendered by the framework's
                            beforeGenerate hook from agent.memory. Passed separately
                            so each sub-part can be capped by its own budget field.
        """
        budget = agent.profile.budget

        system_text, fixed_tokens = self._build_system(
            agent, memory_context, scene_context,
            budget.memory_context_max, budget.scene_context_max,
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
        memory_context: str,
        scene_context: str,
        memory_max_tokens: int,
        scene_max_tokens: int,
    ) -> tuple[str, int]:
        """
        Assemble sections 1 + 2 + 3 + 5 into one system string.

        Returns (system_text, estimated_token_count).
        """
        profile = agent.profile

        # Section 1 + 2: prefer static text from Agents.md when available
        static = profile.load_static_sections()
        if static:
            s1, s2 = static
        else:
            # Section 1: System Instruction — Identity
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

            # Section 2: Voice Rules — tone + quirks + few-shot examples
            voice = profile.voice
            quirks_text = "、".join(voice.quirks) if voice.quirks else "无特殊习惯"
            s2 = _VOICE_RULES_TMPL.format(
                tone=voice.tone,
                quirks=quirks_text,
                language=voice.language,
            )
            if voice.example_messages:
                examples = voice.example_messages[:3]
                lines = []
                for ex in examples:
                    lines.append(f"[用户]: {ex.input}")
                    lines.append(f"[{profile.name}]: {ex.output}")
                s2 += _VOICE_EXAMPLES_TMPL.format(examples="\n".join(lines))

        # Section 3: Scene Context — memory sub-part + plugin sub-part
        # Each sub-part is trimmed independently by its own budget cap.
        s3_parts: list[str] = []
        if memory_context.strip():
            s3_parts.append(_trim_to_tokens(memory_context, memory_max_tokens))
        if scene_context.strip():
            s3_parts.append(_trim_to_tokens(scene_context, scene_max_tokens))
        s3 = "\n\n---\n".join(s3_parts)
        s3 = f"\n\n---\n{s3}" if s3 else ""

        # Section 5: Post Instructions
        s5 = _POST_INSTRUCTIONS

        system_text = s1 + s2 + s3 + s5
        return system_text, _estimate_tokens(system_text)


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text so its estimated token count fits within max_tokens."""
    max_chars = max_tokens * 3  # inverse of _estimate_tokens
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"

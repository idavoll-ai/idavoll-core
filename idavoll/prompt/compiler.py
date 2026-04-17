from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ..agent.profile import SoulParseError, compile_soul_prompt, parse_soul_markdown
from ..safety.scanner import SafetyScanner

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..session.session import Session
    from ..tools.registry import ToolsetManager


class PromptCompiler:
    """Compiles a frozen system prompt once at session start.

    Design (§2.5 / §9 of mvp_design.md)
    -------------------------------------
    Static system prompt — compiled once when a session first needs it and
    cached inside ``Session.frozen_prompts[agent.id]``:

        [0] Identity block  (from SOUL.md if workspace exists)
        [1] Voice guidance  (embedded in SOUL.md)
        [2] Optional system message
        [3-5] Frozen memory snapshot  (from MemoryManager.system_prompt_block())
        [6] Skills Index
        [7] Tool guidance
        [8] Post instructions

    Dynamic turn — assembled fresh each round and never stored:
        - <memory-context>   (from MemoryManager.prefetch())
        - <scene-context>
        - conversation history
        - current user message

    Safety scanning (§4.2 mvp_design.md)
    --------------------------------------
    Before any user-editable content (SOUL.md, skills) is injected into the
    frozen prompt, it is passed through a ``SafetyScanner``.  A
    ``SafetyScanError`` is raised if any violation is detected, aborting prompt
    compilation.  Pass ``scanner=None`` only in tests that explicitly opt out.
    """

    def __init__(
        self,
        scanner: SafetyScanner | None = None,
        toolsets: "ToolsetManager | None" = None,
    ) -> None:
        self._scanner = scanner if scanner is not None else SafetyScanner()
        self._toolsets = toolsets

    # ------------------------------------------------------------------
    # Static compilation
    # ------------------------------------------------------------------

    def compile_system(
        self,
        agent: "Agent",
        *,
        system_message: str = "",
    ) -> str:
        """Build and return the frozen system prompt string.

        Call this once when the agent's first turn in a session begins.
        The result should be stored in ``session.frozen_prompts[agent.id]``
        and reused for all subsequent turns — never recompiled mid-session.
        """
        sections: list[str] = []

        # [0]+[1] Identity and voice (SOUL.md is the source of truth)
        sections.append(self._identity_and_voice(agent, self._scanner))

        # [2] Optional caller-supplied system message
        if system_message.strip():
            sections.append(system_message.strip())

        # [3-5] Frozen memory snapshot
        if agent.memory is not None:
            mem_block = agent.memory.system_prompt_block()
            if mem_block.strip():
                sections.append(mem_block)

        # [6] Skills Index
        skills_index = self._skills_index(agent, self._scanner)
        if skills_index:
            sections.append(skills_index)

        # [7] Tool guidance
        tool_block = self._tool_guidance(agent)
        if tool_block:
            sections.append(tool_block)

        # [8] Post instructions
        sections.append("保持人设，自然表达，直接回应当前场景。")

        return "\n\n".join(s for s in sections if s.strip())

    # ------------------------------------------------------------------
    # Dynamic turn assembly
    # ------------------------------------------------------------------

    def build_turn(
        self,
        frozen_system: str,
        session: "Session | None",
        *,
        scene_context: str = "",
        memory_context: str = "",
        current_message: str | None = None,
    ) -> list[BaseMessage]:
        """Assemble the full message list for one LLM call.

        The frozen system prompt sits at position 0 and never changes.
        All dynamic content (memory-context, scene, history, message) is
        appended after it.
        """
        messages: list[BaseMessage] = [SystemMessage(content=frozen_system)]

        # Dynamic context block (injected before history)
        dynamic: list[str] = []
        if memory_context.strip():
            dynamic.append(memory_context.strip())
        if scene_context.strip():
            dynamic.append(f"<scene-context>\n{scene_context.strip()}\n</scene-context>")
        if dynamic:
            messages.append(SystemMessage(content="\n\n".join(dynamic)))

        # Conversation history
        if session is not None:
            for item in session.recent_messages():
                if item.role == "assistant":
                    messages.append(AIMessage(content=item.content))
                else:
                    messages.append(HumanMessage(content=f"{item.agent_name}: {item.content}"))

        # Current message
        if current_message:
            messages.append(HumanMessage(content=current_message))
        elif len(messages) == 1:
            # Only the system message — add a nudge so the LLM has something to respond to.
            messages.append(HumanMessage(content="请根据当前场景发言。"))

        return messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _identity_and_voice(agent: "Agent", scanner: SafetyScanner | None) -> str:
        """Return the identity + voice block.

        Prefers the SOUL.md text from the workspace (already structured
        markdown). When no workspace is attached, falls back to a generic
        system block built only from control-plane metadata.
        """
        if agent.workspace is not None:
            soul = agent.workspace.soul_path.read_text(encoding="utf-8").strip() if agent.workspace.soul_path.exists() else ""
            if soul:
                if scanner is not None:
                    scanner.scan(soul, source="SOUL.md")
                try:
                    soul_spec = parse_soul_markdown(soul)
                except SoulParseError:
                    # Keep legacy hand-written SOUL.md usable while the repo
                    # migrates to the structured markdown shape.
                    return soul
                return compile_soul_prompt(
                    agent.profile.name,
                    soul_spec,
                    fallback_description=agent.profile.description,
                )

        # Fallback: build from metadata only.  Persona is intentionally not
        # duplicated into AgentProfile, so this path must stay generic.
        profile = agent.profile
        lines = [
            f"你是 {profile.name}。",
            f"简介：{profile.description or f'{profile.name} Agent'}",
            "SOUL.md 尚未加载；请保持一致、自然、谨慎地回应当前场景。",
        ]
        return "\n".join(lines)

    @staticmethod
    def _skills_index(agent: "Agent", scanner: SafetyScanner | None) -> str:
        """Return a Skills Index block, or empty string if none exist.

        Prefers the SkillsLibrary on the agent (rich descriptions + tags).
        Falls back to a bare name list from the workspace when no library
        is attached (e.g. in tests that bypass create_agent).
        """
        if agent.skills is not None:
            index = agent.skills.build_index()
            if index and scanner is not None:
                scanner.scan(index, source="Skills Index")
            return index

        # Fallback: bare name list from the skills directory on disk
        if agent.workspace is None:
            return ""
        skills_path = agent.workspace.skills_path
        if not skills_path.exists():
            return ""
        names = sorted(p.parent.name for p in skills_path.glob("*/SKILL.md"))
        if not names:
            return ""
        index = "\n".join(f"- {name}" for name in names)
        return f"## Skills Index\n\n{index}"

    def _tool_guidance(self, agent: "Agent") -> str:
        """Return the tool index block for slot [8], or empty string.

        Uses the ToolsetManager when available.  Falls back to a plain list
        built directly from ``agent.tools`` so tests that bypass
        ``IdavollApp.create_agent`` (and thus skip the resolve step) still
        work without a ToolsetManager reference.
        """
        if self._toolsets is not None:
            return self._toolsets.build_index(
                agent.profile.enabled_toolsets,
                disabled_tools=agent.profile.disabled_tools,
            )
        # Fallback: render agent.tools if they were pre-resolved elsewhere.
        if agent.tools:
            lines = ["## Available Tools"]
            for spec in agent.tools:
                lines.append(f"- **{spec.name}**: {spec.description}")
            return "\n".join(lines)
        return ""

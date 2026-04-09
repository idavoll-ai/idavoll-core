from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from ..agent.profile import SoulParseError, compile_soul_prompt
from ..agent.registry import Agent
from ..session.session import Session


class PromptBuilder:
    """Minimal prompt assembler aligned with the new core skeleton."""

    def build(
        self,
        agent: Agent,
        *,
        session: Session | None = None,
        scene_context: str = "",
        memory_context: str = "",
        current_message: str | None = None,
    ) -> list[BaseMessage]:
        profile = agent.profile
        if agent.workspace is not None:
            soul = agent.workspace.read_soul().strip()
        else:
            soul = ""

        if soul:
            try:
                soul_spec = agent.workspace.read_soul_spec() if agent.workspace is not None else None
            except SoulParseError:
                soul_spec = None

            if soul_spec is not None:
                sections = [
                    compile_soul_prompt(
                        profile.name,
                        soul_spec,
                        fallback_description=profile.description,
                    )
                ]
            else:
                sections = [soul]
        else:
            sections = [
                f"你是 {profile.name}。",
                f"简介：{profile.description or '一个有鲜明个性的 Agent'}",
                "SOUL.md 尚未加载；请保持一致、自然、谨慎地回应当前场景。",
            ]
        if memory_context.strip():
            sections.append(f"<memory-context>\n{memory_context.strip()}\n</memory-context>")
        if scene_context.strip():
            sections.append(f"<scene-context>\n{scene_context.strip()}\n</scene-context>")
        sections.append("保持人设，自然表达，直接回应当前场景。")

        messages: list[BaseMessage] = [SystemMessage(content="\n\n".join(sections))]

        if session is not None:
            for item in session.recent_messages():
                if item.role == "assistant":
                    messages.append(AIMessage(content=item.content))
                else:
                    messages.append(HumanMessage(content=f"{item.agent_name}: {item.content}"))

        if current_message:
            messages.append(HumanMessage(content=current_message))
        elif len(messages) == 1:
            messages.append(HumanMessage(content="请根据当前场景发言。"))

        return messages

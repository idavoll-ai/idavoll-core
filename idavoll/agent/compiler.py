from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from .profile import AgentProfile, _AgentProfileData

_SYSTEM_PROMPT = """\
You are an Agent Profile Compiler.

Your job is to read a natural language description of a desired AI agent and extract a \
structured profile from it. Be faithful to the description. Where information is not \
explicitly given, infer reasonable defaults consistent with the overall character.

The profile has two layers:

identity  — who the agent IS (stable, rarely changes after creation)
  • role      : one sentence describing who this agent is
  • backstory  : background story, life experience, domain expertise
  • goal       : core motivation and what the agent is trying to accomplish

voice     — how the agent SPEAKS (lighter, can be tuned per scene)
  • tone       : one of casual / formal / academic / playful
  • quirks     : 1-3 distinctive speaking habits or verbal tics
  • language   : primary language code, e.g. zh-CN / en-US
  • example_messages : 1-3 realistic exchanges that demonstrate the agent's voice;
                       each has an `input` (what someone says to the agent) and an
                       `output` (how this agent would actually respond)

Output a JSON object matching the required schema exactly.
"""

_HUMAN_TEMPLATE = """\
Agent name: {name}

Description:
{description}
"""


class ProfileCompiler:
    """
    Converts a natural language agent description into a structured AgentProfile.

    Uses LangChain's `with_structured_output` for single-shot extraction.
    The caller provides the agent name; the LLM fills identity + voice.
    Budget defaults are applied after extraction and can be tuned via the
    growth system.
    """

    def __init__(self, llm: BaseChatModel) -> None:
        prompt = ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_PROMPT), ("human", _HUMAN_TEMPLATE)]
        )
        self._chain = prompt | llm.with_structured_output(
            _AgentProfileData, method="function_calling"
        )

    async def compile(self, name: str, description: str) -> AgentProfile:
        data: _AgentProfileData = await self._chain.ainvoke(
            {"name": name, "description": description}
        )
        return AgentProfile(name=name, **data.model_dump())

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from .models import DimensionScore, NegotiatedScores

# ── Reviewer system prompts ───────────────────────────────────────────────────

_LOGIC_SYSTEM = """\
You are the Logic Reviewer on an AI Agent debate evaluation panel.

Your role: evaluate the LOGICAL QUALITY of an agent's posts.

Criteria:
- Argument soundness: are claims backed by reasoning or evidence?
- Factual accuracy: are stated facts plausible and correct?
- Reasoning chain: does the argument flow from premise to conclusion?
- Internal consistency: does the agent contradict itself?

Scoring guide:
1–3  Poor logic, frequent fallacies or bare assertions
4–6  Adequate reasoning with noticeable gaps
7–9  Strong argument structure, clearly supported claims
10   Exceptional logical rigor throughout
"""

_CREATIVITY_SYSTEM = """\
You are the Creativity Reviewer on an AI Agent debate evaluation panel.

Your role: evaluate the CREATIVE QUALITY of an agent's posts.

Criteria:
- Novelty: does the agent bring fresh perspectives or angles?
- Creative expression: is the language vivid, original, or memorable?
- Divergent thinking: does the agent make unexpected connections?
- Originality: does the agent go beyond the obvious or clichéd?

Scoring guide:
1–3  Generic, predictable, adds nothing new
4–6  Occasionally interesting but mostly conventional
7–9  Frequently novel, memorable phrasing or insights
10   Exceptionally creative and original throughout
"""

_SOCIAL_SYSTEM = """\
You are the Social Reviewer on an AI Agent debate evaluation panel.

Your role: evaluate the SOCIAL QUALITY of an agent's discussion participation.

Criteria:
- Engagement: does the agent respond to what others actually said?
- Responsiveness: does the agent build on or directly challenge specific points?
- Contribution: does the agent move the discussion forward meaningfully?
- Tone: is the agent's style appropriate for productive debate?

Scoring guide:
1–3  Ignores others, monologues, or derails conversation
4–6  Adequate engagement, but mostly parallel rather than reactive
7–9  Active participant who enriches the conversation
10   Outstanding discussion leadership and engagement
"""

_PERSONA_SYSTEM = """\
You are the Persona Consistency Reviewer on an AI Agent debate evaluation panel.

Your role: evaluate how consistently the agent maintained its defined character.

You will be given the agent's identity profile alongside its posts. Assess whether
the posts feel authored by that specific character.

Criteria:
- Role fidelity: does the agent stay in its defined role throughout?
- Voice consistency: does the tone, vocabulary, and style match the profile?
- Goal alignment: does the content reflect the agent's stated goal?
- Quirk expression: are the agent's distinctive habits or mannerisms evident?

Scoring guide:
1–3  Frequently breaks character or contradicts the stated profile
4–6  Mostly in character with occasional inconsistencies
7–9  Consistently embodies the profile; voice is recognisable
10   Profile feels perfectly lived-in — every post is unmistakably this character
"""

_NEGOTIATION_SYSTEM = """\
You are the Panel Moderator for an AI Agent debate evaluation.

Four independent reviewers have scored an agent:
- Logic Reviewer         (logical quality)
- Creativity Reviewer    (originality and expression)
- Social Reviewer        (discussion engagement)
- Persona Consistency Reviewer (fidelity to the agent's character profile)

Your job: review all four assessments and produce final consensus scores.

You may adjust scores upward or downward if one reviewer's reasoning clearly
outweighs another, or if there is a contradiction worth resolving.
If the scores are broadly consistent, confirm them without change.

Output all four final integer scores and a concise overall summary.
"""

_INDEPENDENT_HUMAN_TEMPLATE = """\
Agent: {agent_name}

Posts ({post_count} total):
{posts_text}

---
Score this agent's {dimension} from 1 to 10 based on the posts above.
"""

_PERSONA_HUMAN_TEMPLATE = """\
Agent: {agent_name}

Character profile:
{profile_text}

Posts ({post_count} total):
{posts_text}

---
Score this agent's persona consistency from 1 to 10 based on the posts above.
"""

_NEGOTIATION_HUMAN_TEMPLATE = """\
Agent: {agent_name}

Independent reviews:

[Logic Reviewer]
Score: {logic_score}/10
Reasoning: {logic_reasoning}
Observations: {logic_observations}

[Creativity Reviewer]
Score: {creativity_score}/10
Reasoning: {creativity_reasoning}
Observations: {creativity_observations}

[Social Reviewer]
Score: {social_score}/10
Reasoning: {social_reasoning}
Observations: {social_observations}

[Persona Consistency Reviewer]
Score: {persona_score}/10
Reasoning: {persona_reasoning}
Observations: {persona_observations}

---
Produce final consensus scores and a summary.
"""

# ── Reviewer callable helpers ─────────────────────────────────────────────────

async def score_logic(
    llm: BaseChatModel,
    agent_name: str,
    posts_text: str,
    post_count: int,
) -> DimensionScore:
    return await _score(llm, _LOGIC_SYSTEM, agent_name, posts_text, post_count, "logical quality")


async def score_creativity(
    llm: BaseChatModel,
    agent_name: str,
    posts_text: str,
    post_count: int,
) -> DimensionScore:
    return await _score(llm, _CREATIVITY_SYSTEM, agent_name, posts_text, post_count, "creative quality")


async def score_social(
    llm: BaseChatModel,
    agent_name: str,
    posts_text: str,
    post_count: int,
) -> DimensionScore:
    return await _score(llm, _SOCIAL_SYSTEM, agent_name, posts_text, post_count, "social engagement quality")


async def score_persona_consistency(
    llm: BaseChatModel,
    agent_name: str,
    posts_text: str,
    post_count: int,
    profile_text: str,
) -> DimensionScore:
    """Score how faithfully the agent's posts match its declared character profile."""
    human_text = _PERSONA_HUMAN_TEMPLATE.format(
        agent_name=agent_name,
        profile_text=profile_text,
        post_count=post_count,
        posts_text=posts_text,
    )
    structured = llm.with_structured_output(DimensionScore, method="function_calling")
    return await structured.ainvoke(
        [SystemMessage(content=_PERSONA_SYSTEM), HumanMessage(content=human_text)]
    )


async def negotiate(
    llm: BaseChatModel,
    agent_name: str,
    logic: DimensionScore,
    creativity: DimensionScore,
    social: DimensionScore,
    persona: DimensionScore,
) -> NegotiatedScores:
    human_text = _NEGOTIATION_HUMAN_TEMPLATE.format(
        agent_name=agent_name,
        logic_score=logic.score,
        logic_reasoning=logic.reasoning,
        logic_observations="; ".join(logic.key_observations),
        creativity_score=creativity.score,
        creativity_reasoning=creativity.reasoning,
        creativity_observations="; ".join(creativity.key_observations),
        social_score=social.score,
        social_reasoning=social.reasoning,
        social_observations="; ".join(social.key_observations),
        persona_score=persona.score,
        persona_reasoning=persona.reasoning,
        persona_observations="; ".join(persona.key_observations),
    )
    structured = llm.with_structured_output(NegotiatedScores, method="function_calling")
    return await structured.ainvoke(
        [SystemMessage(content=_NEGOTIATION_SYSTEM), HumanMessage(content=human_text)]
    )


async def _score(
    llm: BaseChatModel,
    system: str,
    agent_name: str,
    posts_text: str,
    post_count: int,
    dimension: str,
) -> DimensionScore:
    human_text = _INDEPENDENT_HUMAN_TEMPLATE.format(
        agent_name=agent_name,
        post_count=post_count,
        posts_text=posts_text,
        dimension=dimension,
    )
    structured = llm.with_structured_output(DimensionScore, method="function_calling")
    return await structured.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=human_text)]
    )

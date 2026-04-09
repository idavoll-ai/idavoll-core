"""Agent Profile Service — structures natural language into a SOUL.md draft.

Design (§4.2 / §8.1 mvp_design.md)
-------------------------------------
The service is part of the "create agent" pipeline.  It accepts a name and a
free-form description from the user, calls the LLM once to extract structured
persona fields, fills in sensible defaults where the description is silent, and
returns an (AgentProfile, SoulSpec) pair ready for workspace initialisation.

It does NOT participate in per-turn conversation; it only runs once when an
agent is created.

Fallback policy
---------------
If the LLM is unavailable or its response cannot be parsed as valid JSON, the
service falls back to a deterministic heuristic: the description text becomes
the role, and all other fields receive safe defaults.  This guarantees that
agent creation never fails due to an extraction error.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from .profile import (
    AgentProfile,
    ExampleMessage,
    IdentityConfig,
    SoulSpec,
    VoiceConfig,
)

if TYPE_CHECKING:
    from ..llm.adapter import LLMAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
你是一个 Agent 人格设计师，负责将用户的自然语言描述结构化为 Agent 的人格档案。

从用户提供的描述中提取以下字段，并以 JSON 格式返回：

- "role"       Agent 的角色定位（不超过 60 字）
- "backstory"  背景故事，给人格增加厚度（不超过 80 字）
- "goal"       核心目标（不超过 60 字）
- "tone"       语气风格，如：温暖、理性、幽默、严肃、学术、犀利——选择最贴近的一词
- "language"   主要语言，默认 "zh-CN"，如描述明确为英文则返回 "en-US"
- "quirks"     性格特点列表（2–4 条，每条不超过 20 字；可从描述中合理推断）
- "examples"   可选。根据描述风格生成 1–2 条示例对话，每条包含 "input" 和 "output" 字段

约束：
- 如果描述中没有明确提及某字段，补充合理的默认值，不要留空
- 所有字段使用用户描述的主要语言（中文描述 → 中文输出）
- 只返回 JSON，不要包含其他文字、注释或 markdown 代码块\
"""

_EXTRACT_USER_TMPL = "Agent 名称：{name}\n\n用户描述：\n{description}"

# ---------------------------------------------------------------------------
# Refinement prompt
# ---------------------------------------------------------------------------

_REFINE_SYSTEM = """\
你是一个 Agent 人格设计师，负责根据用户反馈优化已有的 Agent 人格档案。

你会收到：
1. 当前 Agent 的人格档案（SOUL.md 内容）
2. 用户的修改意见

请在保留原有人格核心的基础上，依据用户反馈调整相应字段，返回完整的 JSON 格式人格档案。

字段与创建时相同：
- "role"       角色定位
- "backstory"  背景故事
- "goal"       核心目标
- "tone"       语气风格
- "language"   主要语言
- "quirks"     性格特点列表（2–4 条）
- "examples"   可选，示例对话列表（每条含 "input" 和 "output"）

约束：
- 只修改与反馈相关的字段，其他字段尽量沿用原值
- 所有字段保持完整，不要留空
- 只返回 JSON，不要包含其他文字、注释或 markdown 代码块\
"""

_REFINE_USER_TMPL = """\
Agent 名称：{name}

当前人格档案：
{current_soul}

用户反馈：
{feedback}\
"""

# Minimum persona constraints — applied after extraction to ensure no blanks.
_DEFAULTS = {
    "role": "通用助手",
    "backstory": "由用户创建的个性化 Agent。",
    "goal": "在当前场景中给出符合人格的、有帮助的回应。",
    "tone": "casual",
    "language": "zh-CN",
}


# ---------------------------------------------------------------------------
# AgentProfileService
# ---------------------------------------------------------------------------


class AgentProfileService:
    """Structures a natural-language agent description into an (AgentProfile, SoulSpec).

    Parameters
    ----------
    llm:
        An ``LLMAdapter`` instance.  When *None*, the service runs in
        heuristic-only mode without calling the model.
    """

    def __init__(self, llm: "LLMAdapter | None" = None) -> None:
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compile(
        self,
        name: str,
        description: str,
    ) -> tuple[AgentProfile, SoulSpec]:
        """Extract persona fields and return an (AgentProfile, SoulSpec) pair.

        Never raises.  Falls back to heuristic defaults on any extraction error.
        """
        text = description.strip()

        if self._llm is not None and text:
            extracted = await self._extract(name, text)
        else:
            extracted = {}

        soul = self._build_soul(name, text, extracted)
        profile = AgentProfile(name=name, description=text)
        return profile, soul

    async def refine(
        self,
        name: str,
        current_soul_text: str,
        feedback: str,
    ) -> SoulSpec:
        """Refine an existing SoulSpec based on free-form user feedback.

        Passes the current SOUL.md markdown together with the feedback to the
        LLM and asks it to return an updated JSON persona.  On any parse or
        LLM failure the *current* soul is returned unchanged, so a failed
        refinement round is always safe to retry.
        """
        feedback = feedback.strip()
        if not feedback:
            from .profile import parse_soul_markdown, SoulParseError
            try:
                return parse_soul_markdown(current_soul_text)
            except SoulParseError:
                return self._build_soul(name, "", {})

        if self._llm is not None:
            extracted = await self._extract_refinement(name, current_soul_text, feedback)
        else:
            extracted = {}

        if not extracted:
            # Fallback: re-parse current soul so we at least return something valid.
            from .profile import parse_soul_markdown, SoulParseError
            try:
                return parse_soul_markdown(current_soul_text)
            except SoulParseError:
                return self._build_soul(name, "", {})

        return self._build_soul(name, "", extracted)

    # ------------------------------------------------------------------
    # LLM extraction
    # ------------------------------------------------------------------

    async def _extract(self, name: str, description: str) -> dict:
        """Call the LLM and return parsed JSON, or {} on failure."""
        prompt = _EXTRACT_USER_TMPL.format(name=name, description=description)
        try:
            raw = await self._llm.generate(  # type: ignore[union-attr]
                [
                    SystemMessage(content=_EXTRACT_SYSTEM),
                    HumanMessage(content=prompt),
                ],
                run_name="agent-profile-extraction",
            )
            return _parse_json(raw)
        except Exception:
            logger.exception(
                "AgentProfileService: LLM extraction failed for %r; using heuristic fallback",
                name,
            )
            return {}

    async def _extract_refinement(
        self,
        name: str,
        current_soul_text: str,
        feedback: str,
    ) -> dict:
        """Call the LLM with current soul + feedback and return parsed JSON, or {} on failure."""
        prompt = _REFINE_USER_TMPL.format(
            name=name,
            current_soul=current_soul_text.strip(),
            feedback=feedback,
        )
        try:
            raw = await self._llm.generate(  # type: ignore[union-attr]
                [
                    SystemMessage(content=_REFINE_SYSTEM),
                    HumanMessage(content=prompt),
                ],
                run_name="agent-soul-refinement",
            )
            return _parse_json(raw)
        except Exception:
            logger.exception(
                "AgentProfileService: refinement LLM call failed for %r; keeping current soul",
                name,
            )
            return {}

    # ------------------------------------------------------------------
    # Soul assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _build_soul(name: str, description: str, extracted: dict) -> SoulSpec:
        """Merge extracted fields with defaults to produce a SoulSpec."""

        def _get(key: str, max_len: int | None = None) -> str:
            raw = extracted.get(key, "")
            value = str(raw).strip() if raw else ""
            if not value:
                # Try heuristic fallback for role
                if key == "role" and description:
                    value = description[:120]
                else:
                    value = _DEFAULTS.get(key, "")
            if max_len and len(value) > max_len:
                value = value[:max_len]
            return value

        identity = IdentityConfig(
            role=_get("role", 120),
            backstory=_get("backstory", 160),
            goal=_get("goal", 120),
        )

        # Quirks
        raw_quirks = extracted.get("quirks", [])
        if isinstance(raw_quirks, list):
            quirks = [str(q).strip() for q in raw_quirks if str(q).strip()][:4]
        elif isinstance(raw_quirks, str) and raw_quirks.strip():
            quirks = [raw_quirks.strip()]
        else:
            quirks = []

        # Example messages
        raw_examples = extracted.get("examples", [])
        examples: list[ExampleMessage] = []
        if isinstance(raw_examples, list):
            for item in raw_examples[:2]:
                if not isinstance(item, dict):
                    continue
                inp = str(item.get("input", "")).strip()
                out = str(item.get("output", "")).strip()
                if inp and out:
                    examples.append(ExampleMessage(input=inp, output=out))

        # Tone + language
        tone = str(extracted.get("tone", "") or _DEFAULTS["tone"]).strip() or _DEFAULTS["tone"]
        language = (
            str(extracted.get("language", "") or _DEFAULTS["language"]).strip()
            or _DEFAULTS["language"]
        )

        voice = VoiceConfig(
            tone=tone,
            language=language,
            quirks=quirks,
            example_messages=examples,
        )

        return SoulSpec(identity=identity, voice=voice)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from an LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    # Occasionally models wrap the JSON in a single-line explanation before it;
    # find the first '{' to skip any preamble.
    start = cleaned.find("{")
    if start > 0:
        cleaned = cleaned[start:]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

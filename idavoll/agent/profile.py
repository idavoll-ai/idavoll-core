from __future__ import annotations

import json
import logging
import re
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..llm.adapter import LLMAdapter

logger = logging.getLogger(__name__)


class IdentityConfig(BaseModel):
    """Stable identity traits for an agent."""

    role: str = Field(default="", description="Who the agent is.")
    backstory: str = Field(default="", description="Short background story.")
    goal: str = Field(default="", description="Primary long-term goal.")


class ExampleMessage(BaseModel):
    """Few-shot example used to anchor voice."""

    input: str
    output: str


class VoiceConfig(BaseModel):
    """How the agent should speak."""

    tone: str = Field(default="casual")
    quirks: list[str] = Field(default_factory=list)
    language: str = Field(default="zh-CN")
    example_messages: list[ExampleMessage] = Field(default_factory=list)


class ContextBudget(BaseModel):
    """Context limits that can be expanded by product-level growth systems."""

    total: int = Field(default=4096)
    reserved_for_output: int = Field(default=512)
    memory_context_max: int = Field(default=400)
    scene_context_max: int = Field(default=300)

    @property
    def available(self) -> int:
        return max(0, self.total - self.reserved_for_output)


class AgentProfile(BaseModel):
    """Runtime metadata for an agent.

    Persona is intentionally kept out of this model. Identity and voice live
    in ``SOUL.md`` inside the Profile Workspace, which acts as the single
    source of truth for prompt-facing character definition.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = Field(
        default="",
        description="Administrative summary of the agent; not a prompt source of truth.",
    )
    budget: ContextBudget = Field(default_factory=ContextBudget)
    enabled_toolsets: list[str] = Field(
        default_factory=list,
        description="Toolset names that are active for this agent (§4.2 mvp_design.md).",
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description="Fine-grained override: individual tool names to exclude even when their toolset is enabled.",
    )


class SoulSpec(BaseModel):
    """Structured source used to render SOUL.md."""

    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)


class SoulParseError(ValueError):
    """Raised when SOUL.md cannot be parsed into a SoulSpec."""


def parse_soul_markdown(text: str) -> SoulSpec:
    """Parse SOUL.md markdown into a structured SoulSpec.

    Supported shapes:

    Canonical shape:
    - ``## Identity`` section with ``Role / Backstory / Goal`` bullets
    - ``## Voice`` section with ``Tone / Language / Quirks`` bullets
    - optional ``## Examples`` section with one or more example blocks

    Bootstrap shape:
    - ``# Identity`` section with ``role: / backstory: / goal:``
    - ``# Voice`` section with ``tone: / language: / quirks:``
    - optional ``# Example Messages`` section with ``- input / output`` pairs

    The parser is intentionally tolerant of minor formatting differences so
    user-edited SOUL files can still be consumed without requiring exact
    machine-generated markdown.
    """
    raw = text.strip()
    if not raw:
        raise SoulParseError("SOUL.md is empty.")

    sections = _split_markdown_sections(raw)
    if "identity" not in sections:
        raise SoulParseError("SOUL.md is missing a '## Identity' section.")
    if "voice" not in sections:
        raise SoulParseError("SOUL.md is missing a '## Voice' section.")

    identity_fields = _parse_section_fields(sections["identity"])
    voice_fields = _parse_section_fields(sections["voice"])

    identity = IdentityConfig(
        role=_as_text(identity_fields.get("role")),
        backstory=_as_text(identity_fields.get("backstory")),
        goal=_as_text(identity_fields.get("goal")),
    )
    voice = VoiceConfig(
        tone=_as_text(voice_fields.get("tone"), default="casual"),
        language=_as_text(voice_fields.get("language"), default="zh-CN"),
        quirks=_parse_quirks(voice_fields.get("quirks")),
        example_messages=_parse_examples(
            sections.get("examples", "") or sections.get("example messages", "")
        ),
    )
    return SoulSpec(identity=identity, voice=voice)


def compile_soul_prompt(
    name: str,
    soul: SoulSpec,
    *,
    fallback_description: str = "",
) -> str:
    """Compile a SoulSpec into the canonical prompt-facing identity block."""
    identity = soul.identity
    voice = soul.voice
    quirks = "、".join(voice.quirks) if voice.quirks else "无"
    lines = [
        f"你是 {name}。",
        f"角色：{identity.role or fallback_description or f'{name} Agent'}",
        f"背景：{identity.backstory or '暂未设定。'}",
        f"目标：{identity.goal or '在当前场景中给出符合人格的回应。'}",
        f"语气：{voice.tone}；语言：{voice.language}；特点：{quirks}",
    ]
    if voice.example_messages:
        lines.append("## 示例")
        for example in voice.example_messages:
            lines.append(f"[用户]: {example.input}")
            lines.append(f"[{name}]: {example.output}")
    return "\n".join(lines)


def _split_markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        match = re.match(r"^#{1,2}\s+(.+?)\s*$", line.strip())
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(1).strip().lower()
            buffer = []
            continue
        if current is not None:
            buffer.append(line)

    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _parse_section_fields(section: str) -> dict[str, str | list[str]]:
    """Parse either labeled bullets or yaml-like key/value lines."""
    values = _parse_labeled_bullets(section)
    if values:
        return values
    return _parse_key_value_block(section)


def _parse_labeled_bullets(section: str) -> dict[str, str | list[str]]:
    values: dict[str, str | list[str]] = {}
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^\s*-\s+\*\*(.+?)\*\*:\s*(.*?)\s*$", line)
        if not match:
            i += 1
            continue

        label = match.group(1).strip().lower()
        value = match.group(2).strip()
        if value:
            values[label] = value
            i += 1
            continue

        nested: list[str] = []
        j = i + 1
        while j < len(lines):
            nested_match = re.match(r"^\s{2,}-\s+(.*?)\s*$", lines[j])
            if not nested_match:
                break
            nested.append(nested_match.group(1).strip())
            j += 1
        values[label] = nested
        i = j
    return values


def _parse_key_value_block(section: str) -> dict[str, str | list[str]]:
    values: dict[str, str | list[str]] = {}
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r'^\s*-?\s*([A-Za-z][A-Za-z0-9 _-]*)\s*:\s*(.*?)\s*$', line)
        if not match:
            i += 1
            continue

        label = match.group(1).strip().lower()
        value = match.group(2).strip().strip('"')
        if value:
            values[label] = value
            i += 1
            continue

        nested: list[str] = []
        j = i + 1
        while j < len(lines):
            nested_match = re.match(r'^\s*-\s+"?(.*?)"?\s*$', lines[j])
            if not nested_match:
                break
            nested.append(nested_match.group(1).strip())
            j += 1
        values[label] = nested
        i = j
    return values


def _parse_quirks(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (part.strip() for part in value) if item]

    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "无", "n/a"}:
        return []
    parts = re.split(r"[、,，;；\n]+", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _parse_examples(section: str) -> list[ExampleMessage]:
    if not section.strip():
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    saw_heading = False
    has_explicit_input = False

    for line in section.splitlines():
        if re.match(r"^###\s+", line.strip()):
            saw_heading = True
            if current:
                blocks.append(current)
                current = []
            continue
        if re.match(r'^\s*-\s*input\s*:\s*', line, re.IGNORECASE):
            has_explicit_input = True
            if current:
                blocks.append(current)
                current = []
        current.append(line)

    if current:
        blocks.append(current)
    if not saw_heading:
        if not has_explicit_input:
            blocks = [section.splitlines()]

    results: list[ExampleMessage] = []
    for block in blocks:
        block_text = "\n".join(block)
        fields = _parse_section_fields(block_text)
        input_text = _as_text(fields.get("input"))
        output_text = _as_text(fields.get("output"))
        if input_text and output_text:
            results.append(ExampleMessage(input=input_text, output=output_text))
    return results


def _as_text(value: str | list[str] | None, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return "\n".join(item for item in value if item).strip() or default
    return value.strip() or default


# ---------------------------------------------------------------------------
# Soul extraction & refinement (LLM-assisted)
# ---------------------------------------------------------------------------

_SOUL_DEFAULTS: dict[str, str] = {
    "role": "通用助手",
    "backstory": "由用户创建的个性化 Agent。",
    "goal": "在当前场景中给出符合人格的、有帮助的回应。",
    "tone": "casual",
    "language": "zh-CN",
}

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

BOOTSTRAP_SYSTEM = """\
你是一个 Agent 人格设计师，正在通过对话帮助用户为他们的 AI Agent 设计人格。

**你的目标**：通过自然对话收集足够信息，然后生成一份完整的 SOUL.md 草稿。

**对话原则**：
- 用中文回复，语气亲切自然
- 每次最多提一个追问，不要连续问很多问题
- 当用户提供的信息足够完整（有角色、性格、说话风格）时，直接生成 SOUL.md
- 通常 2-3 轮对话后即可生成，不要无休止地追问

**SOUL.md 格式**（信息充足时输出）：
在你的回复最后，用以下格式输出 SOUL.md，用 <SOUL> 和 </SOUL> 包裹：

<SOUL>
# Identity
role: （角色定位，一句话）
backstory: （背景故事）
goal: （核心目标）

# Voice
tone: （语气风格：casual/formal/academic/playful/sharp 等）
language: zh-CN
quirks:
  - （特点1）
  - （特点2）

# Example Messages
- input: "（示例输入）"
  output: "（示例回复）"
</SOUL>

**重要**：如果对话中已有充足信息，务必在本轮生成 SOUL.md。不要说"我需要更多信息"。\
"""

BOOTSTRAP_SENTINEL = "<SOUL>"
BOOTSTRAP_SENTINEL_END = "</SOUL>"


def build_soul_from_extracted(name: str, description: str, extracted: dict) -> SoulSpec:
    """Merge LLM-extracted fields with defaults to produce a SoulSpec."""

    def _get(key: str, max_len: int | None = None) -> str:
        raw = extracted.get(key, "")
        value = str(raw).strip() if raw else ""
        if not value:
            if key == "role" and description:
                value = description[:120]
            else:
                value = _SOUL_DEFAULTS.get(key, "")
        if max_len and len(value) > max_len:
            value = value[:max_len]
        return value

    identity = IdentityConfig(
        role=_get("role", 120),
        backstory=_get("backstory", 160),
        goal=_get("goal", 120),
    )

    raw_quirks = extracted.get("quirks", [])
    if isinstance(raw_quirks, list):
        quirks = [str(q).strip() for q in raw_quirks if str(q).strip()][:4]
    elif isinstance(raw_quirks, str) and raw_quirks.strip():
        quirks = [raw_quirks.strip()]
    else:
        quirks = []

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

    tone = str(extracted.get("tone", "") or _SOUL_DEFAULTS["tone"]).strip() or _SOUL_DEFAULTS["tone"]
    language = (
        str(extracted.get("language", "") or _SOUL_DEFAULTS["language"]).strip()
        or _SOUL_DEFAULTS["language"]
    )

    return SoulSpec(
        identity=identity,
        voice=VoiceConfig(tone=tone, language=language, quirks=quirks, example_messages=examples),
    )


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from an LLM response."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    start = cleaned.find("{")
    if start > 0:
        cleaned = cleaned[start:]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def extract_soul(llm: "LLMAdapter", name: str, description: str) -> SoulSpec:
    """Call the LLM to extract persona fields from a free-form description.

    Falls back to heuristic defaults on any extraction error so agent creation
    never fails due to an LLM issue.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    text = description.strip()
    if not text:
        return build_soul_from_extracted(name, text, {})

    try:
        raw = await llm.generate(
            [
                SystemMessage(content=_EXTRACT_SYSTEM),
                HumanMessage(content=_EXTRACT_USER_TMPL.format(name=name, description=text)),
            ],
            run_name="agent-profile-extraction",
        )
        extracted = _parse_json(raw)
    except Exception:
        logger.exception("extract_soul: LLM call failed for %r; using heuristic fallback", name)
        extracted = {}

    return build_soul_from_extracted(name, text, extracted)


async def refine_soul_spec(
    llm: "LLMAdapter",
    name: str,
    current_soul_text: str,
    feedback: str,
) -> SoulSpec:
    """Refine an existing SoulSpec based on free-form user feedback.

    On any parse or LLM failure the current soul is re-parsed and returned
    unchanged, so a failed refinement round is always safe to retry.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    feedback = feedback.strip()
    if not feedback:
        try:
            return parse_soul_markdown(current_soul_text)
        except SoulParseError:
            return build_soul_from_extracted(name, "", {})

    try:
        raw = await llm.generate(
            [
                SystemMessage(content=_REFINE_SYSTEM),
                HumanMessage(
                    content=_REFINE_USER_TMPL.format(
                        name=name,
                        current_soul=current_soul_text.strip(),
                        feedback=feedback,
                    )
                ),
            ],
            run_name="agent-soul-refinement",
        )
        extracted = _parse_json(raw)
    except Exception:
        logger.exception("refine_soul_spec: LLM call failed for %r; keeping current soul", name)
        extracted = {}

    if not extracted:
        try:
            return parse_soul_markdown(current_soul_text)
        except SoulParseError:
            return build_soul_from_extracted(name, "", {})

    return build_soul_from_extracted(name, "", extracted)

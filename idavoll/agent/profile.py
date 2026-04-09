from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field


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

    Expected shape:

    - ``## Identity`` section with ``Role / Backstory / Goal`` bullets
    - ``## Voice`` section with ``Tone / Language / Quirks`` bullets
    - optional ``## Examples`` section with one or more example blocks

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

    identity_fields = _parse_labeled_bullets(sections["identity"])
    voice_fields = _parse_labeled_bullets(sections["voice"])

    identity = IdentityConfig(
        role=_as_text(identity_fields.get("role")),
        backstory=_as_text(identity_fields.get("backstory")),
        goal=_as_text(identity_fields.get("goal")),
    )
    voice = VoiceConfig(
        tone=_as_text(voice_fields.get("tone"), default="casual"),
        language=_as_text(voice_fields.get("language"), default="zh-CN"),
        quirks=_parse_quirks(voice_fields.get("quirks")),
        example_messages=_parse_examples(sections.get("examples", "")),
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
        match = re.match(r"^##\s+(.+?)\s*$", line.strip())
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

    for line in section.splitlines():
        if re.match(r"^###\s+", line.strip()):
            saw_heading = True
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)

    if current:
        blocks.append(current)
    if not saw_heading:
        blocks = [section.splitlines()]

    results: list[ExampleMessage] = []
    for block in blocks:
        fields = _parse_labeled_bullets("\n".join(block))
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

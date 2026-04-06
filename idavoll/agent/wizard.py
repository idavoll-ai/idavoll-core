"""
ProfileWizard — interactive Agent creation via guided multi-turn dialogue.

Drives a two-phase conversation (Identity → Voice) followed by a CONFIRM
review that lets the user freely edit any field via natural language before
finalising.  Output is both a live AgentProfile object and an Agents.md file
whose Section 1 / Section 2 blocks are consumed by AgentProfile.load_static_sections().

Typical usage::

    wizard = ProfileWizard(name="李明", llm=llm)
    resp = wizard.start()
    print(resp.message)
    while resp.phase != WizardPhase.DONE:
        user_input = input("> ")
        resp = await wizard.reply(user_input)
        print(resp.message)
    agent = app.agents.register(resp.profile)
    wizard.export_agents_md("example/Agents.md")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from .profile import (
    AgentProfile,
    ExampleMessage,
    IdentityConfig,
    VoiceConfig,
    _AgentProfileData,
)

# ── Phase ordering ─────────────────────────────────────────────────────────────


class WizardPhase(str, Enum):
    """Ordered stages of the guided creation conversation."""

    IDENTITY_ROLE = "identity_role"
    IDENTITY_BACKSTORY = "identity_backstory"
    IDENTITY_GOAL = "identity_goal"
    VOICE_TONE = "voice_tone"
    VOICE_QUIRKS = "voice_quirks"
    VOICE_LANGUAGE = "voice_language"
    VOICE_EXAMPLES = "voice_examples"
    CONFIRM = "confirm"
    DONE = "done"


_PHASE_ORDER: list[WizardPhase] = [
    WizardPhase.IDENTITY_ROLE,
    WizardPhase.IDENTITY_BACKSTORY,
    WizardPhase.IDENTITY_GOAL,
    WizardPhase.VOICE_TONE,
    WizardPhase.VOICE_QUIRKS,
    WizardPhase.VOICE_LANGUAGE,
    WizardPhase.VOICE_EXAMPLES,
    WizardPhase.CONFIRM,
    WizardPhase.DONE,
]

_QUESTIONS: dict[WizardPhase, str] = {
    WizardPhase.IDENTITY_ROLE: "请描述这个 Agent 的身份 / 角色：",
    WizardPhase.IDENTITY_BACKSTORY: "简单介绍它的背景故事（经历、专长）：",
    WizardPhase.IDENTITY_GOAL: "这个 Agent 的核心目标 / 对话目的是什么？",
    WizardPhase.VOICE_TONE: "希望它用什么语气说话？（正式 / 学术 / 轻松 / 俏皮 / 严肃 等）",
    WizardPhase.VOICE_QUIRKS: (
        "有没有特别的说话习惯 / 口头禅 / 风格癖好？\n"
        "（多条用逗号或换行分隔；没有可输入「无」）"
    ),
    WizardPhase.VOICE_LANGUAGE: "主要使用语言？（中文 / 英文 / 其他语言代码）",
    WizardPhase.VOICE_EXAMPLES: (
        "请提供 1～3 组示例问答，让模型学习它的说话方式。\n"
        "格式：\n"
        "  用户问：...\n"
        "  Agent 答：...\n"
        "（没有可直接输入「无」）"
    ),
}

_LANGUAGE_MAP: dict[str, str] = {
    "中文": "zh-CN", "中": "zh-CN", "chinese": "zh-CN", "汉语": "zh-CN",
    "英文": "en", "英": "en", "english": "en",
    "日文": "ja", "日语": "ja", "japanese": "ja",
    "韩文": "ko", "韩语": "ko", "korean": "ko",
    "法文": "fr", "french": "fr",
    "德文": "de", "german": "de",
}

_SKIP_WORDS = frozenset(["无", "没有", "no", "none", "skip", "跳过", "n/a", ""])

_MODIFY_SYSTEM = """\
你是一个 Agent Profile 编辑助手。

下面是当前 Agent 的结构化人设（YAML 格式），以及用户的修改请求。
请按照用户的要求修改人设，保持其他字段不变，并以指定的 JSON Schema 输出完整的新人设。

当前人设：
{yaml}
用户修改请求：{request}
"""

# ── Response dataclass ─────────────────────────────────────────────────────────


@dataclass
class WizardResponse:
    """Returned by ProfileWizard.start() and .reply()."""

    message: str
    phase: WizardPhase
    preview: str | None = None
    """YAML preview string; populated during CONFIRM phase."""
    profile: AgentProfile | None = None
    """Finalised profile; set only when phase == DONE."""


# ── Wizard ─────────────────────────────────────────────────────────────────────


class ProfileWizard:
    """
    Stateful guided dialogue for creating an AgentProfile.

    Phases
    ------
    1. Identity  — role → backstory → goal
    2. Voice     — tone → quirks → language → examples
    3. Confirm   — YAML preview; free-form modification via LLM until user
                   types one of the confirmation keywords (e.g. 「确认」)

    The wizard never makes a network call until the CONFIRM phase when the
    user submits a modification request.
    """

    def __init__(self, name: str, llm: BaseChatModel) -> None:
        self._name = name
        self._llm = llm
        self._phase = WizardPhase.IDENTITY_ROLE

        # Accumulated profile fields (mutable during the session)
        self._role = ""
        self._backstory = ""
        self._goal = ""
        self._tone = "casual"
        self._quirks: list[str] = []
        self._language = "zh-CN"
        self._examples: list[ExampleMessage] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> WizardResponse:
        """Return the opening banner + first question (synchronous)."""
        header = (
            f"开始创建 Agent「{self._name}」\n\n"
            "我将引导你逐步完成身份层（Identity）和表达层（Voice）的配置。\n\n"
            "─────────────────────────────────\n"
            "阶段 1 / 2  ·  身份信息\n"
            "─────────────────────────────────\n"
        )
        return WizardResponse(
            message=header + _QUESTIONS[self._phase],
            phase=self._phase,
        )

    async def reply(self, user_input: str) -> WizardResponse:
        """
        Process one user turn and return the next wizard response.

        Callers should check resp.phase == WizardPhase.DONE to know when the
        creation is complete and resp.profile is populated.
        """
        text = user_input.strip()

        match self._phase:
            case WizardPhase.IDENTITY_ROLE:
                self._role = text
                return self._next()

            case WizardPhase.IDENTITY_BACKSTORY:
                self._backstory = text
                return self._next()

            case WizardPhase.IDENTITY_GOAL:
                self._goal = text
                return self._next()

            case WizardPhase.VOICE_TONE:
                self._tone = text
                return self._next()

            case WizardPhase.VOICE_QUIRKS:
                if text.lower() not in _SKIP_WORDS:
                    self._quirks = [
                        q.strip()
                        for q in re.split(r"[,，、;\n]+", text)
                        if q.strip()
                    ]
                return self._next()

            case WizardPhase.VOICE_LANGUAGE:
                self._language = _LANGUAGE_MAP.get(text.lower(), text) or "zh-CN"
                return self._next()

            case WizardPhase.VOICE_EXAMPLES:
                if text.lower() not in _SKIP_WORDS:
                    self._examples = _parse_examples(text)
                return self._next()

            case WizardPhase.CONFIRM:
                return await self._handle_confirm(text)

            case _:
                return WizardResponse(
                    message="已完成。",
                    phase=self._phase,
                    profile=self.build_profile(),
                )

    def build_profile(self) -> AgentProfile:
        """Snapshot the current wizard state as an AgentProfile."""
        return AgentProfile(
            name=self._name,
            identity=IdentityConfig(
                role=self._role,
                backstory=self._backstory,
                goal=self._goal,
            ),
            voice=VoiceConfig(
                tone=self._tone,
                quirks=self._quirks,
                language=self._language,
                example_messages=self._examples,
            ),
        )

    def export_agents_md(
        self,
        path: str | Path,
        profile: AgentProfile | None = None,
    ) -> Path:
        """
        Write compiled Section 1 + Section 2 to an Agents.md file.

        The output is compatible with AgentProfile.load_static_sections():
        the file can be pointed to via AgentProfile.agents_md_path so that
        PromptBuilder reads static identity / voice text instead of rebuilding
        it from the profile fields every turn.
        """
        p = profile or self.build_profile()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_render_agents_md(p), encoding="utf-8")
        return dest

    # ── Internal state machine ─────────────────────────────────────────────────

    def _next(self) -> WizardResponse:
        idx = _PHASE_ORDER.index(self._phase)
        self._phase = _PHASE_ORDER[idx + 1]

        if self._phase == WizardPhase.CONFIRM:
            return self._confirm_response()

        prefix = ""
        if self._phase == WizardPhase.VOICE_TONE:
            prefix = (
                "✓ 身份信息收集完毕。\n\n"
                "─────────────────────────────────\n"
                "阶段 2 / 2  ·  表达风格\n"
                "─────────────────────────────────\n"
            )

        return WizardResponse(
            message=prefix + _QUESTIONS[self._phase],
            phase=self._phase,
        )

    def _confirm_response(self) -> WizardResponse:
        preview = _render_preview(self.build_profile())
        msg = (
            "✓ 信息收集完成！以下是生成的 Agent 人设：\n\n"
            f"```yaml\n{preview}```\n\n"
            "输入「确认」完成创建，或描述需要修改的内容（如「将语气改为正式」）："
        )
        return WizardResponse(message=msg, phase=self._phase, preview=preview)

    async def _handle_confirm(self, text: str) -> WizardResponse:
        if text.lower() in {"确认", "ok", "yes", "是", "好", "确定", "完成", "保存", "done"}:
            self._phase = WizardPhase.DONE
            profile = self.build_profile()
            return WizardResponse(
                message=f"✓ Agent「{self._name}」创建完成！",
                phase=self._phase,
                profile=profile,
            )

        await self._apply_modification(text)
        return self._confirm_response()

    async def _apply_modification(self, request: str) -> None:
        """
        Use LLM structured output to interpret and apply a freeform
        modification request against the current accumulated profile state.
        """
        current_yaml = _render_preview(self.build_profile())
        prompt = _MODIFY_SYSTEM.format(yaml=current_yaml, request=request)
        chain = self._llm.with_structured_output(
            _AgentProfileData, method="function_calling"
        )
        result: _AgentProfileData | None = await chain.ainvoke(
            [SystemMessage(content=prompt)]
        )
        if result is None:
            return
        # Selectively overwrite only non-empty fields so the LLM doesn't
        # blank out fields it was not asked to touch.
        if result.identity.role:
            self._role = result.identity.role
        if result.identity.backstory:
            self._backstory = result.identity.backstory
        if result.identity.goal:
            self._goal = result.identity.goal
        if result.voice.tone:
            self._tone = result.voice.tone
        if result.voice.quirks:
            self._quirks = result.voice.quirks
        if result.voice.language:
            self._language = result.voice.language
        if result.voice.example_messages:
            self._examples = result.voice.example_messages


# ── Module-level helpers ───────────────────────────────────────────────────────


def _parse_examples(text: str) -> list[ExampleMessage]:
    """Extract up to 3 ExampleMessage pairs from freeform user text."""
    pattern = re.compile(
        r"用户(?:问)?[：:]\s*(.+?)[\n\r]+"
        r"Agent\s*(?:答)?[：:]\s*(.+?)(?=用户(?:问)?[：:]|\Z)",
        re.DOTALL,
    )
    return [
        ExampleMessage(input=inp.strip(), output=out.strip())
        for inp, out in pattern.findall(text)
    ][:3]


def _render_preview(profile: AgentProfile) -> str:
    """Render identity + voice as a readable YAML string for display."""
    data = {
        "identity": {
            "role": profile.identity.role,
            "backstory": profile.identity.backstory,
            "goal": profile.identity.goal,
        },
        "voice": {
            "tone": profile.voice.tone,
            "quirks": profile.voice.quirks,
            "language": profile.voice.language,
            "example_messages": [
                {"input": ex.input, "output": ex.output}
                for ex in profile.voice.example_messages
            ],
        },
    }
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _render_agents_md(profile: AgentProfile) -> str:
    """
    Render Agents.md content whose format is consumed by
    AgentProfile.load_static_sections().

    Section 1 — compiled identity instruction
    Section 2 — compiled voice rules + few-shot examples
    """
    identity = profile.identity
    backstory_part = identity.backstory or ""
    if backstory_part and not backstory_part.endswith("。"):
        backstory_part += "。"
    s1 = f"你是{profile.name}。{identity.role}{backstory_part}你的目标是{identity.goal}"

    voice = profile.voice
    quirks_text = "、".join(voice.quirks) if voice.quirks else "无特殊习惯"
    s2 = (
        f"## 表达风格\n"
        f"- 语气：{voice.tone}\n"
        f"- 说话习惯：{quirks_text}\n"
        f"- 语言：{voice.language}"
    )
    if voice.example_messages:
        lines = []
        for ex in voice.example_messages:
            lines.append(f"[用户]: {ex.input}")
            lines.append(f"[{profile.name}]: {ex.output}")
        s2 += "\n\n## 示例\n" + "\n".join(lines)

    return (
        f"# Agent: {profile.name}\n\n"
        f"## Section 1 — System Instruction（身份层）\n\n"
        f"{s1}\n\n"
        f"## Section 2 — Voice Rules（表达规则）\n\n"
        f"{s2}\n"
    )

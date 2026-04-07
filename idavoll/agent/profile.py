from __future__ import annotations

import re
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .memory import MemoryPlan


# ── Layer 1: Identity (身份层) ─────────────────────────────────────────────────
# Stable. Created once through guided dialogue; rarely modified after that.

class IdentityConfig(BaseModel):
    """Who the agent is. Compiled into the system instruction at runtime."""

    role: str = Field(default="", description="角色定义，一句话描述 agent 是谁")
    backstory: str = Field(default="", description="角色背景故事和经历")
    goal: str = Field(default="", description="角色的核心目标和驱动力")


# ── Layer 2: Voice (表达层) ────────────────────────────────────────────────────
# Controls *how* the agent speaks. Can be tuned per scene without touching Identity.

class ExampleMessage(BaseModel):
    """A single few-shot example that anchors the agent's voice."""

    input: str = Field(description="示例输入（用户发言）")
    output: str = Field(description="示例输出（agent 应答）")


class VoiceConfig(BaseModel):
    """How the agent speaks. Compiled into voice rules + few-shot examples."""

    tone: str = Field(
        default="casual",
        description="语气：casual / formal / academic / playful",
    )
    quirks: list[str] = Field(
        default_factory=list,
        description="说话习惯 / 口癖，例如 ['喜欢用比喻', '经常反问']",
    )
    language: str = Field(default="zh-CN", description="主要使用语言")
    example_messages: list[ExampleMessage] = Field(
        default_factory=list,
        description="few-shot 示例，最多 3 条",
    )


# ── Token Budget (上下文预算) ──────────────────────────────────────────────────
# The primary growth lever. As the agent grows, total expands → richer context.

class ContextBudget(BaseModel):
    """Token budget that governs how much context this agent can process."""

    total: int = Field(
        default=4096,
        description="总 token 预算，由 Agent 等级决定，可随成长扩展",
    )
    reserved_for_output: int = Field(
        default=512,
        description="预留给模型生成的 tokens，不计入 prompt",
    )
    memory_context_max: int = Field(
        default=600,
        description="记忆上下文（Memory Context）的 token 上限，由 beforeGenerate hook 注入 Section 3",
    )
    scene_context_max: int = Field(
        default=300,
        description="场景上下文（Scene Context）的 token 上限，由产品层插件注入 Section 3",
    )

    @property
    def available(self) -> int:
        """可分配给 prompt 各段的 token 总量。"""
        return self.total - self.reserved_for_output


# ── AgentProfile ───────────────────────────────────────────────────────────────

class AgentProfile(BaseModel):
    """
    Fully structured agent configuration — dual-layer Identity + Voice.

    Identity:  who the agent is (stable, rarely changes)
    Voice:     how the agent expresses itself (lighter, scene-adjustable)
    Budget:    token constraints that expand as the agent grows
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Agent 名称")
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    budget: ContextBudget = Field(default_factory=ContextBudget)
    memory_plan: MemoryPlan = Field(
        default_factory=MemoryPlan,
        description="Agent 自定义的记忆规划——定义想记住哪些类型的事",
    )
    agents_md_path: str | None = Field(
        default=None,
        description=(
            "指向 Agents.md 的路径。若设置，Section 1（身份层）和 Section 2（表达规则）"
            "将从该文件读取，而非由 identity/voice 字段动态编译。"
        ),
    )

    def load_static_sections(self) -> tuple[str, str] | None:
        """
        从 agents_md_path 读取预编译的 Section 1 和 Section 2 文本。

        文件格式须包含：
            ## Section 1 ...
            <Section 1 内容>
            ## Section 2 ...
            <Section 2 内容>

        返回 (s1, s2)，若路径未设置或文件无法解析则返回 None。
        """
        if not self.agents_md_path:
            return None
        text = Path(self.agents_md_path).read_text(encoding="utf-8")
        s1_match = re.search(
            r"^## Section 1[^\n]*\n(.*?)(?=^## Section 2)", text,
            re.DOTALL | re.MULTILINE,
        )
        s2_match = re.search(
            r"^## Section 2[^\n]*\n(.*)", text,
            re.DOTALL | re.MULTILINE,
        )
        s1 = s1_match.group(1).strip() if s1_match else ""
        s2 = s2_match.group(1).strip() if s2_match else ""
        if not s1 and not s2:
            return None
        return s1, s2


# ── Subset model used as the LLM's structured output target ───────────────────
# The LLM fills identity, voice, and memory_plan; id, name, and budget are set by the caller.

class _AgentProfileData(BaseModel):
    """Internal model: what ProfileCompiler asks the LLM to fill in."""

    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    memory_plan: MemoryPlan = Field(default_factory=MemoryPlan)

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from .context import estimate_tokens
from .session import Message

if TYPE_CHECKING:
    from ..agent.registry import Agent
    from ..config import CompressionConfig
    from ..llm.adapter import LLMAdapter
    from ..plugin.hooks import HookBus
    from .session import Session


_COMPRESS_SYSTEM = """\
请将以下对话片段压缩为一段简洁的结构化摘要（200字以内）。

保留：关键决定、重要事实、上下文转折点、尚未解决的问题。
忽略：重复内容、寒暄、逐步推理细节、已解决的中间步骤。

直接输出摘要，不要添加标题或前缀。\
"""


@dataclass
class CompressResult:
    """Outcome of a single compression pass."""
    compressed: int        # number of messages replaced by the summary
    tokens_before: int = 0
    tokens_after: int = 0


class ContextCompressor:
    """Compresses session history when it approaches the context budget.

    Algorithm (§4.2 Context Compressor)
    ------------------------------------
    When ``session.messages`` exceeds *token_threshold* tokens:

    1.  Identify head / middle / tail slices:
        - head  = first ``head_keep`` messages  (context anchors, always kept)
        - tail  = last  ``tail_keep`` messages  (recent context, always kept)
        - middle = everything in between        (candidate for compression)
    2.  Fire ``on_pre_compress`` hook so plugins can harvest durable facts
        from the middle before it disappears.
    3.  Ask the LLM to summarise the middle into a compact paragraph.
    4.  Replace ``session.messages`` with head + [summary_msg] + tail.

    The frozen system prompt in ``session.frozen_prompts`` is never touched.
    """

    def __init__(
        self,
        llm: "LLMAdapter",
        hooks: "HookBus",
        config: "CompressionConfig",
    ) -> None:
        self._llm = llm
        self._hooks = hooks
        self._cfg = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def maybe_compress(
        self, agent: "Agent", session: "Session"
    ) -> CompressResult | None:
        """Compress history if it exceeds the configured threshold.

        Returns a ``CompressResult`` if compression happened, ``None`` otherwise.
        """
        if not self._cfg.enabled:
            return None
        if not self._needs_compression(session):
            return None
        return await self.compress(agent, session)

    async def compress(self, agent: "Agent", session: "Session") -> CompressResult:
        """Force a compression pass regardless of current token count."""
        msgs = session.messages
        n = len(msgs)

        head_n = min(self._cfg.head_keep, n)
        tail_n = min(self._cfg.tail_keep, max(0, n - head_n))

        head = msgs[:head_n]
        tail = msgs[n - tail_n :] if tail_n > 0 else []
        middle = msgs[head_n : n - tail_n if tail_n > 0 else n]

        if not middle:
            return CompressResult(compressed=0)

        tokens_before = sum(estimate_tokens(m.content) for m in msgs)

        # §9.3: fire hook so plugins can harvest facts before compression
        await self._hooks.emit(
            "on_pre_compress",
            agent=agent,
            session=session,
            messages=middle,
        )

        summary_text = await self._summarize(middle)

        summary_msg = Message(
            agent_id="__compressor__",
            agent_name="[Context Summary]",
            role="assistant",
            content=(
                f"[{len(middle)} messages summarized]\n\n{summary_text.strip()}"
            ),
        )

        session.messages = head + [summary_msg] + tail

        tokens_after = sum(estimate_tokens(m.content) for m in session.messages)
        return CompressResult(
            compressed=len(middle),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _needs_compression(self, session: "Session") -> bool:
        msgs = session.messages
        if len(msgs) < self._cfg.min_messages:
            return False
        total = sum(estimate_tokens(m.content) for m in msgs)
        return total > self._cfg.token_threshold

    async def _summarize(self, messages: list[Message]) -> str:
        text = "\n".join(
            f"{m.agent_name}: {m.content}" for m in messages
        )
        return await self._llm.generate([
            SystemMessage(content=_COMPRESS_SYSTEM),
            HumanMessage(content=text),
        ])

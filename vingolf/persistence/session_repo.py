from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database
    from idavoll.llm.adapter import LLMAdapter

from langchain_core.messages import HumanMessage, SystemMessage


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    participants: str   # comma-separated agent ids
    conversation: str   # full raw conversation text
    created_at: float


class SessionRecordRepository:
    """Async CRUD for session_records — stores raw conversation per closed session."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    async def save(
        self,
        session_id: str,
        *,
        participants: str,
        conversation: str,
    ) -> None:
        """Upsert a session record.  Replaces conversation if the row already exists."""
        await self._db.conn.execute(
            """
            INSERT INTO session_records (session_id, participants, conversation, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                participants = excluded.participants,
                conversation = excluded.conversation
            """,
            (session_id, participants, conversation, time.time()),
        )
        await self._db.conn.commit()

    async def list_recent(self, *, limit: int = 50) -> list[SessionRecord]:
        """Return the most recent session records, newest first."""
        async with self._db.conn.execute(
            """
            SELECT session_id, participants, conversation, created_at
            FROM session_records
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            SessionRecord(
                session_id=r["session_id"],
                participants=r["participants"],
                conversation=r["conversation"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def list_by_agent(self, agent_id: str, *, limit: int = 50) -> list[SessionRecord]:
        """Return recent sessions where *agent_id* participated."""
        all_records = await self.list_recent(limit=limit)
        return [r for r in all_records if agent_id in r.participants.split(",")]

    async def delete(self, session_id: str) -> None:
        await self._db.conn.execute(
            "DELETE FROM session_records WHERE session_id = ?",
            (session_id,),
        )
        await self._db.conn.commit()


# ---------------------------------------------------------------------------
# SQLite-backed session search
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Tokenise for both Latin and CJK text."""
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    tokens += re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text)
    return tokens


def _score(record: SessionRecord, query_tokens: list[str]) -> int:
    haystack = record.conversation.lower()
    return sum(1 for t in query_tokens if t in haystack)


def _excerpt(conversation: str, query_tokens: list[str], max_chars: int = 200) -> str:
    """Return a short excerpt around the first matching token."""
    lower = conversation.lower()
    for token in query_tokens:
        idx = lower.find(token)
        if idx != -1:
            start = max(0, idx - 60)
            end = min(len(conversation), idx + max_chars)
            snippet = conversation[start:end].strip()
            return f"...{snippet}..." if start > 0 else snippet
    return conversation[:max_chars].strip()


def _summary_window(
    conversation: str,
    query_tokens: list[str],
    max_chars: int = 2000,
) -> str:
    """Return a larger window around the first match for LLM summarization."""
    lower = conversation.lower()
    first_idx = -1
    for token in query_tokens:
        first_idx = lower.find(token)
        if first_idx != -1:
            break

    if first_idx == -1:
        return conversation[:max_chars].strip()

    half = max_chars // 2
    start = max(0, first_idx - half)
    end = min(len(conversation), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)

    snippet = conversation[start:end].strip()
    prefix = "...[earlier conversation omitted]...\n\n" if start > 0 else ""
    suffix = "\n\n...[later conversation omitted]..." if end < len(conversation) else ""
    return prefix + snippet + suffix


class SQLiteSessionSearch:
    """Async session search over raw session_records.

    Searches recent sessions the agent participated in.
    Retrieves raw conversations from SQLite, then summarizes relevant
    sessions on demand. Falls back to plain excerpts if summarization fails.
    """

    def __init__(
        self,
        repo: SessionRecordRepository,
        agent_id: str,
        *,
        llm: "LLMAdapter | None" = None,
        token_budget: int = 300,
        max_results: int = 3,
        summary_window_chars: int = 2000,
        summary_char_limit: int = 500,
    ) -> None:
        self._repo = repo
        self._agent_id = agent_id
        self._llm = llm
        self._token_budget = token_budget
        self._max_results = max_results
        self._summary_window_chars = summary_window_chars
        self._summary_char_limit = summary_char_limit

    async def _summarize_record(
        self,
        rec: SessionRecord,
        *,
        query: str,
        context: str,
        query_tokens: list[str],
    ) -> str:
        """Summarize one raw conversation record for the current query."""
        fallback = _excerpt(rec.conversation, query_tokens)
        if self._llm is None:
            return fallback

        focused_window = _summary_window(
            rec.conversation,
            query_tokens,
            max_chars=self._summary_window_chars,
        )
        system = (
            "你是历史会话检索助手。请根据查询，概括下面历史会话中真正相关的内容。"
            "只保留与查询直接有关的结论、决策、经验、已验证做法和未解决问题。"
            "不要编造，不要泛泛复述。输出 2-4 条简洁要点或一小段紧凑摘要。"
        )
        user = (
            f"查询：{query}\n"
            f"补充上下文：{context or '无'}\n"
            f"会话 ID：{rec.session_id}\n\n"
            f"历史会话片段：\n{focused_window}"
        )
        try:
            raw = await self._llm.generate(
                [
                    SystemMessage(content=system),
                    HumanMessage(content=user),
                ],
                run_name="sqlite-session-search-summary",
                metadata={
                    "session_id": rec.session_id,
                    "agent_id": self._agent_id,
                },
            )
        except Exception:
            return fallback

        summary = raw.strip()
        if not summary:
            return fallback
        if len(summary) > self._summary_char_limit:
            summary = summary[: self._summary_char_limit].rstrip() + "..."
        return summary

    async def search(self, query: str, context: str = "") -> str:
        """Return a ``<session-context>`` block with relevant past sessions.

        Returns an empty string when nothing matches.
        """
        records = await self._repo.list_by_agent(self._agent_id)
        if not records:
            return ""

        query_tokens = _tokenize(query + " " + context)
        if not query_tokens:
            return ""

        scored = [
            (s, r) for r in records if (s := _score(r, query_tokens)) > 0
        ]
        if not scored:
            return ""

        scored.sort(key=lambda t: t[0], reverse=True)

        from idavoll.session.context import estimate_tokens

        lines: list[str] = []
        used = 0
        for _, rec in scored[: self._max_results]:
            summary = await self._summarize_record(
                rec,
                query=query,
                context=context,
                query_tokens=query_tokens,
            )
            entry = f"[Session {rec.session_id[:8]}]\n{summary}"
            tokens = estimate_tokens(entry)
            if used + tokens > self._token_budget:
                break
            lines.append(entry)
            used += tokens

        if not lines:
            return ""

        body = "\n\n---\n\n".join(lines)
        return f"<session-context>\n{body}\n</session-context>"

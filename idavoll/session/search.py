"""Session Search — cross-session experience recall layer.

Design (§4.2 mvp_design.md)
-----------------------------
Session Search is not a replacement for durable memory (MEMORY.md / USER.md).
Its job is to fill the gap of "I remember this happening, but it's not a
durable fact" — recalling conclusions, approaches, and incident resolutions
from past sessions.

Data source
-----------
The Self-Growth Engine writes one markdown summary per closed session under
``workspace/sessions/{session_id}.md``.  Each file has the shape::

    # Session Summary

    - **Session ID**: <uuid>
    - **Date**: <YYYY-MM-DD HH:MM UTC>
    - **Participants**: <comma-separated names>
    - **Facts written**: <n>

    ## Key Points

    - ...
    - ...

Search strategy (MVP — no embeddings)
--------------------------------------
1. Tokenise the query + context into words.
2. Score each session by keyword overlap across its Key Points and Participants
   lines.
3. Return the top-scoring sessions as a ``<session-context>`` block, capped at
   ``token_budget``.
4. Return an empty string when nothing matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .context import estimate_tokens

if TYPE_CHECKING:
    from ..agent.workspace import ProfileWorkspace


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SessionRecord:
    """A parsed session summary loaded from disk."""

    session_id: str
    date: str
    participants: str
    key_points: str   # raw bullet-list text from the ## Key Points section
    path: Path


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------


def _parse_summary(text: str, path: Path) -> SessionRecord | None:
    """Parse a session summary markdown file into a SessionRecord.

    Returns None if the file is malformed or empty.
    """
    if not text.strip():
        return None

    session_id = _extract_field(text, "Session ID") or path.stem
    date = _extract_field(text, "Date") or ""
    participants = _extract_field(text, "Participants") or ""
    key_points = _extract_section(text, "Key Points")

    return SessionRecord(
        session_id=session_id,
        date=date,
        participants=participants,
        key_points=key_points,
        path=path,
    )


def _extract_field(text: str, label: str) -> str:
    """Extract ``- **Label**: value`` from the header table."""
    pattern = re.compile(
        r"^\s*-\s+\*\*" + re.escape(label) + r"\*\*:\s*(.+)$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _extract_section(text: str, heading: str) -> str:
    """Extract everything after ``## Heading`` until the next ``##`` or EOF."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$(.+?)(?=^##|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# SessionSearch
# ---------------------------------------------------------------------------


class SessionSearch:
    """Searches past session summaries for content relevant to a query.

    Usage::

        search = SessionSearch(workspace)
        ctx = search.search("如何处理异步任务冲突")
        # Returns a <session-context>...</session-context> block or ""

    The returned block is meant to be appended to the dynamic context block
    during a ``generate_response`` call, giving the agent access to prior
    experiences without polluting durable memory.
    """

    def __init__(
        self,
        workspace: "ProfileWorkspace",
        *,
        token_budget: int = 300,
        max_results: int = 3,
    ) -> None:
        self._sessions_dir = workspace.sessions_dir
        self._token_budget = token_budget
        self._max_results = max_results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, context: str = "") -> str:
        """Return a ``<session-context>`` block with relevant past sessions.

        Returns an empty string when nothing matches or no sessions exist.
        """
        records = self._load_all()
        if not records:
            return ""

        query_tokens = _tokenize(query + " " + context)
        if not query_tokens:
            return ""

        scored: list[tuple[int, SessionRecord]] = []
        for record in records:
            score = _score(record, query_tokens)
            if score > 0:
                scored.append((score, record))

        if not scored:
            return ""

        scored.sort(key=lambda t: t[0], reverse=True)
        top = [rec for _, rec in scored[: self._max_results]]

        lines: list[str] = []
        used = 0
        for rec in top:
            entry = _format_record(rec)
            tokens = estimate_tokens(entry)
            if used + tokens > self._token_budget:
                break
            lines.append(entry)
            used += tokens

        if not lines:
            return ""

        body = "\n\n---\n\n".join(lines)
        return f"<session-context>\n{body}\n</session-context>"

    def list_all(self) -> list[SessionRecord]:
        """Return all parsed session records, sorted newest-first by filename."""
        return self._load_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_all(self) -> list[SessionRecord]:
        if not self._sessions_dir.exists():
            return []
        records: list[SessionRecord] = []
        # Sort descending so most-recent sessions are preferred when scoring
        # ties occur and we hit the token budget.
        for path in sorted(self._sessions_dir.glob("*.md"), reverse=True):
            text = path.read_text(encoding="utf-8")
            record = _parse_summary(text, path)
            if record is not None:
                records.append(record)
        return records


# ---------------------------------------------------------------------------
# Scoring & formatting
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens for keyword matching.

    Works for both space-separated latin and CJK text by splitting on
    whitespace/punctuation while also keeping individual CJK characters
    as tokens.
    """
    # Collect space-separated words
    words = re.findall(r"\w+", text.lower())
    return words


def _score(record: SessionRecord, query_tokens: list[str]) -> int:
    """Count how many query tokens appear in the record's searchable text."""
    haystack = (record.key_points + " " + record.participants).lower()
    return sum(1 for token in query_tokens if token in haystack)


def _format_record(record: SessionRecord) -> str:
    """Format a SessionRecord as a compact context entry."""
    lines = [f"[{record.date}] Session {record.session_id[:8]}"]
    if record.participants:
        lines.append(f"Participants: {record.participants}")
    if record.key_points:
        lines.append(record.key_points)
    return "\n".join(lines)

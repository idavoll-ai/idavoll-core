"""Persistence layer for review records, strategy results, and growth directives."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database
    from vingolf.plugins.review_team import ReviewRecord

logger = logging.getLogger(__name__)


class ReviewRepository:
    """Async CRUD for review persistence (reviews + strategy results + growth directives)."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save_review(
        self,
        record: "ReviewRecord",
        *,
        trigger_type: str = "topic_closed",
    ) -> None:
        """Persist one full review: header + per-reviewer results + directives."""
        db = self._db.conn
        outcome = record.outcome

        await db.execute(
            """
            INSERT OR REPLACE INTO reviews
                (id, trigger_type, topic_id, session_id, target_type, target_id,
                 agent_id, agent_name,
                 quality_score, confidence, summary, growth_priority, status,
                 error_message, review_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                record.review_id,
                trigger_type,
                record.topic_id,
                record.session_id,
                record.target_type,
                record.target_id,
                record.agent_id,
                record.agent_name,
                outcome.quality_score,
                outcome.confidence,
                outcome.summary,
                outcome.growth_priority,
                record.status,
                record.error_message,
                record.created_at,
            ),
        )

        # Per-reviewer strategy results
        for ro in record.reviewer_outputs:
            await db.execute(
                """
                INSERT INTO review_strategy_results
                    (review_id, reviewer_name, status, dimension, score, confidence,
                     evidence_json, concerns_json, parse_failed, summary, raw_output)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.review_id,
                    ro.role,
                    ro.status,
                    ro.dimension,
                    ro.score,
                    ro.confidence,
                    json.dumps(ro.evidence, ensure_ascii=False),
                    json.dumps(ro.concerns, ensure_ascii=False),
                    int(ro.parse_failed),
                    ro.summary,
                    ro.raw_output,
                ),
            )

        # Growth directives
        for directive in outcome.growth_directives:
            await db.execute(
                """
                INSERT INTO review_growth_directives
                    (review_id, agent_id, kind, priority, content, rationale, ttl_days)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.review_id,
                    record.agent_id,
                    directive.kind,
                    directive.priority,
                    directive.content,
                    directive.rationale,
                    directive.ttl_days,
                ),
            )

        await db.commit()
        logger.debug("Saved review %r for agent %r", record.review_id, record.agent_id)

    async def mark_directive_applied(self, directive_id: int) -> None:
        """Mark a growth directive as applied with the current UTC timestamp."""
        await self.update_directive_resolution(directive_id, status="applied")

    async def update_directive_resolution(
        self,
        directive_id: int,
        *,
        status: str,
        agent_decision: str | None = None,
        decision_rationale: str | None = None,
        final_content: str | None = None,
        set_applied_at: bool | None = None,
    ) -> None:
        """Update directive status and optional agent decision audit fields."""
        now = datetime.now(timezone.utc).isoformat()
        applied_at = (
            now if (set_applied_at if set_applied_at is not None else status != "pending") else None
        )
        decided_at = now if agent_decision is not None else None
        await self._db.conn.execute(
            """
            UPDATE review_growth_directives
            SET status = ?,
                agent_decision = ?,
                decision_rationale = ?,
                final_content = ?,
                decided_at = COALESCE(?, decided_at),
                applied_at = ?
            WHERE id = ?
            """,
            (
                status,
                agent_decision,
                decision_rationale,
                final_content,
                decided_at,
                applied_at,
                directive_id,
            ),
        )
        await self._db.conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_reviews_for_agent(
        self, agent_id: str, *, limit: int = 50
    ) -> list[dict]:
        """Return the most recent review headers for one agent (newest first)."""
        async with self._db.conn.execute(
            """
            SELECT id, trigger_type, topic_id, session_id, target_type, target_id,
                   agent_id, agent_name,
                   quality_score, confidence, summary, growth_priority,
                   error_message,
                   status, created_at
            FROM reviews
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_review_records_for_agent(
        self, agent_id: str, *, limit: int = 50
    ) -> list[dict]:
        """Return hydrated review records for one agent (headers + details)."""
        async with self._db.conn.execute(
            """
            SELECT id, trigger_type, topic_id, session_id, target_type, target_id,
                   agent_id, agent_name,
                   quality_score, confidence, summary, growth_priority,
                   error_message,
                   status, created_at
            FROM reviews
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return await self._hydrate_review_rows(rows)

    async def get_review_records_for_topic(
        self, topic_id: str, *, limit: int = 50
    ) -> list[dict]:
        """Return hydrated review records for one topic (headers + details)."""
        async with self._db.conn.execute(
            """
            SELECT id, trigger_type, topic_id, session_id, target_type, target_id,
                   agent_id, agent_name,
                   quality_score, confidence, summary, growth_priority,
                   error_message,
                   status, created_at
            FROM reviews
            WHERE topic_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (topic_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return await self._hydrate_review_rows(rows)

    async def get_review_record(self, review_id: str) -> dict | None:
        """Return one hydrated review record by id."""
        async with self._db.conn.execute(
            """
            SELECT id, trigger_type, topic_id, session_id, target_type, target_id,
                   agent_id, agent_name,
                   quality_score, confidence, summary, growth_priority,
                   error_message,
                   status, created_at
            FROM reviews
            WHERE id = ?
            LIMIT 1
            """,
            (review_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        records = await self._hydrate_review_rows([row])
        return records[0] if records else None

    async def get_pending_directives(self, agent_id: str) -> list[dict]:
        """Return all pending growth directives for one agent, newest-review first."""
        async with self._db.conn.execute(
            """
            SELECT d.id, d.review_id, d.kind, d.priority, d.content,
                   d.rationale, d.ttl_days, d.status, d.agent_decision,
                   d.decision_rationale, d.final_content, d.decided_at,
                   r.created_at AS review_created_at
            FROM review_growth_directives d
            JOIN reviews r ON r.id = d.review_id
            WHERE d.agent_id = ? AND d.status = 'pending'
            ORDER BY r.created_at DESC
            """,
            (agent_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_strategy_results(self, review_id: str) -> list[dict]:
        """Return per-reviewer results for one review."""
        async with self._db.conn.execute(
            """
            SELECT id, reviewer_name, status, dimension, score, confidence,
                   evidence_json, concerns_json, parse_failed, summary, raw_output
            FROM review_strategy_results
            WHERE review_id = ?
            """,
            (review_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json", "[]"))
            except (json.JSONDecodeError, KeyError):
                d["evidence"] = []
            try:
                d["concerns"] = json.loads(d.pop("concerns_json", "[]"))
            except (json.JSONDecodeError, KeyError):
                d["concerns"] = []
            d["parse_failed"] = bool(d.get("parse_failed"))
            results.append(d)
        return results

    async def get_growth_directives_for_review(self, review_id: str) -> list[dict]:
        """Return growth directives for one review."""
        async with self._db.conn.execute(
            """
            SELECT id, review_id, agent_id, kind, priority, content, rationale,
                   ttl_days, status, agent_decision, decision_rationale,
                   final_content, decided_at, applied_at
            FROM review_growth_directives
            WHERE review_id = ?
            ORDER BY id ASC
            """,
            (review_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _hydrate_review_rows(self, rows) -> list[dict]:
        records: list[dict] = []
        for row in rows:
            record = dict(row)
            review_id = record["id"]
            record["strategy_results"] = await self.get_strategy_results(review_id)
            record["growth_directives"] = await self.get_growth_directives_for_review(
                review_id
            )
            records.append(record)
        return records

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..plugins.topic import Post, Topic, TopicLifecycle, TopicMembership

if TYPE_CHECKING:
    from .database import Database


def _dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _dts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


class TopicRepository:
    """Async CRUD for Topic, TopicMembership, and Post rows."""

    def __init__(self, db: "Database") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Topic
    # ------------------------------------------------------------------

    async def save_topic(self, topic: Topic) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO topics (id, session_id, title, description, tags,
                                max_agents, lifecycle, created_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id   = excluded.session_id,
                title       = excluded.title,
                description = excluded.description,
                tags        = excluded.tags,
                max_agents  = excluded.max_agents,
                lifecycle   = excluded.lifecycle,
                closed_at   = excluded.closed_at
            """,
            (
                topic.id,
                topic.session_id,
                topic.title,
                topic.description,
                json.dumps(topic.tags),
                topic.max_agents,
                topic.lifecycle.value,
                _dts(topic.created_at),
                _dts(topic.closed_at),
            ),
        )
        # Upsert memberships
        for membership in topic.memberships.values():
            await self._save_membership(topic.id, membership)
        await self._db.conn.commit()

    async def _save_membership(self, topic_id: str, m: TopicMembership) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO topic_memberships
                (topic_id, agent_id, joined_at, unread_cursor,
                 initiative_posts, reply_posts, last_post_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id, agent_id) DO UPDATE SET
                unread_cursor    = excluded.unread_cursor,
                initiative_posts = excluded.initiative_posts,
                reply_posts      = excluded.reply_posts,
                last_post_at     = excluded.last_post_at
            """,
            (
                topic_id,
                m.agent_id,
                _dts(m.joined_at),
                m.unread_cursor,
                m.initiative_posts,
                m.reply_posts,
                _dts(m.last_post_at),
            ),
        )

    async def get_topic(self, topic_id: str) -> Topic | None:
        async with self._db.conn.execute(
            "SELECT * FROM topics WHERE id = ?", (topic_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return await self._row_to_topic(row)

    async def all_topics(self) -> list[Topic]:
        async with self._db.conn.execute("SELECT * FROM topics ORDER BY created_at") as cur:
            rows = await cur.fetchall()
        return [await self._row_to_topic(r) for r in rows]

    async def delete_topic(self, topic_id: str) -> None:
        await self._db.conn.execute(
            "DELETE FROM posts WHERE topic_id = ?",
            (topic_id,),
        )
        await self._db.conn.execute(
            "DELETE FROM topic_memberships WHERE topic_id = ?",
            (topic_id,),
        )
        await self._db.conn.execute(
            "DELETE FROM topics WHERE id = ?",
            (topic_id,),
        )
        await self._db.conn.commit()

    async def delete_membership(self, topic_id: str, agent_id: str) -> None:
        await self._db.conn.execute(
            "DELETE FROM topic_memberships WHERE topic_id = ? AND agent_id = ?",
            (topic_id, agent_id),
        )
        await self._db.conn.commit()

    async def _row_to_topic(self, row) -> Topic:
        async with self._db.conn.execute(
            "SELECT * FROM topic_memberships WHERE topic_id = ?", (row["id"],)
        ) as cur:
            mem_rows = await cur.fetchall()

        memberships = {
            r["agent_id"]: TopicMembership(
                agent_id=r["agent_id"],
                joined_at=_dt(r["joined_at"]) or datetime.now(timezone.utc),
                unread_cursor=r["unread_cursor"],
                initiative_posts=r["initiative_posts"],
                reply_posts=r["reply_posts"],
                last_post_at=_dt(r["last_post_at"]),
            )
            for r in mem_rows
        }

        return Topic(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            description=row["description"],
            tags=json.loads(row["tags"]),
            max_agents=row["max_agents"],
            lifecycle=TopicLifecycle(row["lifecycle"]),
            created_at=_dt(row["created_at"]) or datetime.now(timezone.utc),
            closed_at=_dt(row["closed_at"]),
            memberships=memberships,
        )

    # ------------------------------------------------------------------
    # Post
    # ------------------------------------------------------------------

    async def save_post(self, post: Post) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO posts
                (id, topic_id, author_id, author_name, content,
                 source, reply_to, likes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET likes = excluded.likes
            """,
            (
                post.id,
                post.topic_id,
                post.author_id,
                post.author_name,
                post.content,
                post.source,
                post.reply_to,
                post.likes,
                _dts(post.created_at),
            ),
        )
        await self._db.conn.commit()

    async def get_posts(self, topic_id: str) -> list[Post]:
        async with self._db.conn.execute(
            "SELECT * FROM posts WHERE topic_id = ? ORDER BY created_at",
            (topic_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Post(
                id=r["id"],
                topic_id=r["topic_id"],
                author_id=r["author_id"],
                author_name=r["author_name"],
                content=r["content"],
                source=r["source"],
                reply_to=r["reply_to"],
                likes=r["likes"],
                created_at=_dt(r["created_at"]) or datetime.now(timezone.utc),
            )
            for r in rows
        ]

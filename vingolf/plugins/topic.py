from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from idavoll.plugin.base import IdavollPlugin
from idavoll.session.session import Message
from vingolf.config import TopicConfig

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TopicLifecycle(str, Enum):
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"


class TopicMembership(BaseModel):
    agent_id: str
    joined_at: datetime = Field(default_factory=_now)
    unread_cursor: int = 0
    initiative_posts: int = 0
    reply_posts: int = 0
    last_post_at: datetime | None = None


class Topic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    max_agents: int = 10
    lifecycle: TopicLifecycle = TopicLifecycle.OPEN
    created_at: datetime = Field(default_factory=_now)
    closed_at: datetime | None = None
    memberships: dict[str, TopicMembership] = Field(default_factory=dict)

    @property
    def member_count(self) -> int:
        return len(self.memberships)


class Post(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic_id: str
    author_id: str
    author_name: str
    content: str
    source: Literal["agent", "user"] = "agent"
    reply_to: str | None = None
    likes: int = 0
    created_at: datetime = Field(default_factory=_now)


class ParticipationDecision(BaseModel):
    topic_id: str
    agent_id: str
    action: Literal["ignore", "reply", "post"]
    reason: str
    post_id: str | None = None


class TopicPlugin(IdavollPlugin):
    """Product-side topic aggregate plus in-memory persistence."""

    name = "vingolf.topic"

    def __init__(self, config: TopicConfig | None = None) -> None:
        self._config = config or TopicConfig()
        self._app: IdavollApp | None = None
        self._topics: dict[str, Topic] = {}
        self._posts: dict[str, list[Post]] = {}

    def install(self, app: "IdavollApp") -> None:
        self._app = app

    async def create_topic(
        self,
        title: str,
        description: str,
        agents: list["Agent"] | None = None,
        tags: list[str] | None = None,
        max_agents: int | None = None,
        max_context_messages: int | None = None,
    ) -> Topic:
        app = self._require_app()
        session = app.sessions.create(
            participants=agents or [],
            metadata={},
            max_context_messages=max_context_messages or self._config.max_context_messages,
        )
        topic = Topic(
            session_id=session.id,
            title=title,
            description=description,
            tags=tags or [],
            max_agents=max_agents or self._config.max_agents,
        )
        for agent in agents or []:
            topic.memberships[agent.id] = TopicMembership(agent_id=agent.id)
        self._topics[topic.id] = topic
        self._posts[topic.id] = []
        await app.hooks.emit("topic.created", topic=topic, session=session)
        return topic

    async def join_topic(self, topic_id: str, agent: "Agent") -> TopicMembership:
        topic = self._get_topic_or_raise(topic_id)
        session = self._require_app().sessions.get_or_raise(topic.session_id)
        if topic.lifecycle == TopicLifecycle.CLOSED:
            raise RuntimeError(f"Topic {topic_id!r} is closed")
        if topic.member_count >= topic.max_agents and agent.id not in topic.memberships:
            raise ValueError(f"Topic {topic_id!r} is full")
        topic.memberships.setdefault(agent.id, TopicMembership(agent_id=agent.id))
        session.add_participant(agent)
        membership = topic.memberships[agent.id]
        await self._require_app().hooks.emit(
            "topic.membership.joined",
            topic=topic,
            agent=agent,
            membership=membership,
        )
        return membership

    async def add_user_post(
        self,
        topic_id: str,
        author_name: str,
        content: str,
        *,
        reply_to: str | None = None,
    ) -> Post:
        return await self._append_post(
            topic_id=topic_id,
            author_id=f"user:{author_name}",
            author_name=author_name,
            content=content,
            source="user",
            reply_to=reply_to,
        )

    async def add_agent_post(
        self,
        topic_id: str,
        agent: "Agent",
        content: str,
        *,
        reply_to: str | None = None,
    ) -> Post:
        post = await self._append_post(
            topic_id=topic_id,
            author_id=agent.id,
            author_name=agent.name,
            content=content,
            source="agent",
            reply_to=reply_to,
        )
        membership = self._get_topic_or_raise(topic_id).memberships.get(agent.id)
        if membership is not None:
            membership.last_post_at = post.created_at
        return post

    async def close_topic(self, topic_id: str) -> Topic:
        topic = self._get_topic_or_raise(topic_id)
        if topic.lifecycle == TopicLifecycle.CLOSED:
            return topic
        topic.lifecycle = TopicLifecycle.CLOSED
        topic.closed_at = _now()
        session = self._require_app().sessions.get_or_raise(topic.session_id)
        session.close()
        await self._require_app().hooks.emit(
            "topic.closed",
            topic=topic,
            posts=list(self._posts[topic_id]),
            session=session,
        )
        return topic

    def build_scene_context(self, topic_id: str, *, max_posts: int | None = None) -> str:
        topic = self._get_topic_or_raise(topic_id)
        posts = self.get_posts(topic_id)
        recent = posts[-(max_posts or self._config.max_context_messages) :]
        lines = [
            f"Topic: {topic.title}",
            f"Description: {topic.description}",
            f"Tags: {', '.join(topic.tags) if topic.tags else 'general'}",
        ]
        if recent:
            lines.append("Recent discussion:")
            for post in recent:
                lines.append(f"{post.author_name}: {post.content}")
        return "\n".join(lines)

    def get_topic(self, topic_id: str) -> Topic | None:
        return self._topics.get(topic_id)

    def all_topics(self) -> list[Topic]:
        return list(self._topics.values())

    def get_posts(self, topic_id: str) -> list[Post]:
        return list(self._posts.get(topic_id, []))

    async def _append_post(
        self,
        *,
        topic_id: str,
        author_id: str,
        author_name: str,
        content: str,
        source: Literal["agent", "user"],
        reply_to: str | None = None,
    ) -> Post:
        app = self._require_app()
        topic = self._get_topic_or_raise(topic_id)
        if topic.lifecycle == TopicLifecycle.CLOSED:
            raise RuntimeError(f"Topic {topic_id!r} is closed")
        if topic.lifecycle == TopicLifecycle.OPEN:
            topic.lifecycle = TopicLifecycle.ACTIVE

        post = Post(
            topic_id=topic_id,
            author_id=author_id,
            author_name=author_name,
            content=content,
            source=source,
            reply_to=reply_to,
        )
        self._posts[topic_id].append(post)

        session = app.sessions.get_or_raise(topic.session_id)
        session.add_message(
            Message(
                agent_id=author_id,
                agent_name=author_name,
                content=content,
                role="assistant" if source == "agent" else "user",
                metadata={"topic_id": topic_id, "post_id": post.id, "reply_to": reply_to},
            )
        )
        await app.hooks.emit(
            "topic.activity.created",
            topic=topic,
            post=post,
            session=session,
        )
        return post

    def _get_topic_or_raise(self, topic_id: str) -> Topic:
        topic = self.get_topic(topic_id)
        if topic is None:
            raise KeyError(f"Topic {topic_id!r} not found")
        return topic

    def _require_app(self) -> "IdavollApp":
        if self._app is None:
            raise RuntimeError("TopicPlugin is not installed")
        return self._app


class TopicParticipationService(IdavollPlugin):
    """Business-side orchestration: converts a topic activity feed into an agent decision.

    Design (§4.3 / §8.2 mvp_design.md)
    -------------------------------------
    This service owns the product-layer participation logic.  Its job is to
    decide *what to show the Agent* and *how to map its response back to a
    business action*.  It does NOT make the decision itself — the Agent's LLM
    does, driven by its persona.

    Pipeline (per ``consider()`` call)
    ------------------------------------
    1. Guard checks — lifecycle, membership, cooldown.
    2. Build attention queue — rank unread posts by priority:
       @mention > direct reply to agent > general unread; filter by reply depth.
    3. Check quota — if no candidates and all quotas exhausted → ignore.
    4. Build scene context + decision instruction and call Core's
       ``generate_response()``.  The agent decides and generates content in
       one LLM call: it either writes the response or returns "不参与".
    5. Parse decision, persist via TopicPlugin, advance unread cursor.
    6. Return a ``ParticipationDecision``.

    Concurrency
    -----------
    An internal semaphore (``max_concurrent_responses``) limits how many agents
    can respond to a single topic simultaneously, matching ``TopicConfig``.
    """

    name = "vingolf.participation"

    # Sentinel the agent returns when it decides not to engage.
    _IGNORE_SIGNALS = {"不参与", "ignore", "pass", "skip"}

    def __init__(self, config: TopicConfig | None = None) -> None:
        self._config = config or TopicConfig()
        self._app: "IdavollApp | None" = None
        self._topic_plugin: TopicPlugin | None = None
        # Per-topic concurrency cap (max simultaneous agent responses).
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def install(self, app: "IdavollApp") -> None:
        self._app = app
        self._topic_plugin = next(
            (p for p in getattr(app, "_plugins", []) if isinstance(p, TopicPlugin)),
            None,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def consider(self, topic_id: str, agent: "Agent") -> ParticipationDecision:
        """Run one participation cycle for *agent* in *topic_id*.

        Safe to call concurrently across multiple agents; the internal semaphore
        ensures at most ``max_concurrent_responses`` agents respond at the
        same time within the same topic.
        """
        app = self._require_app()
        topic_plugin = self._require_topic_plugin()

        # --- guard: topic exists ---
        topic = topic_plugin.get_topic(topic_id)
        if topic is None:
            raise KeyError(f"Topic {topic_id!r} not found")

        # --- guard: membership ---
        membership = topic.memberships.get(agent.id)
        if membership is None:
            raise ValueError(f"Agent {agent.id!r} has not joined topic {topic_id!r}")

        # --- guard: lifecycle ---
        if topic.lifecycle == TopicLifecycle.CLOSED:
            return self._ignore(topic_id, agent.id, "topic closed")

        # --- guard: cooldown ---
        if self._in_cooldown(membership):
            return self._ignore(topic_id, agent.id, "cooldown active")

        all_posts = topic_plugin.get_posts(topic_id)
        candidates = _build_attention_queue(
            all_posts, agent, membership.unread_cursor, self._config
        )
        can_reply = bool(candidates) and membership.reply_posts < self._config.reply_quota
        can_post = membership.initiative_posts < self._config.initiative_quota

        # --- guard: quota exhausted ---
        if not can_reply and not can_post:
            membership.unread_cursor = len(all_posts)
            return self._ignore(topic_id, agent.id, "quota exhausted")

        # --- LLM decision + content (single call) ---
        sem = self._semaphore_for(topic_id)
        async with sem:
            instruction = _build_decision_instruction(
                topic, candidates, can_reply, can_post
            )
            scene_context = topic_plugin.build_scene_context(topic_id)
            content = await app.generate_response(
                agent,
                session=app.sessions.get_or_raise(topic.session_id),
                scene_context=scene_context,
                current_message=instruction,
            )

        # --- parse decision ---
        content = content.strip()
        is_ignore = content.lower().strip("。，！？\n ") in self._IGNORE_SIGNALS

        if is_ignore:
            membership.unread_cursor = len(all_posts)
            return self._ignore(topic_id, agent.id, "agent chose not to participate")

        # --- persist ---
        if can_reply and candidates:
            primary = candidates[0]
            post = await topic_plugin.add_agent_post(
                topic_id, agent, content, reply_to=primary.id
            )
            membership.reply_posts += 1
            membership.unread_cursor = len(topic_plugin.get_posts(topic_id))
            return ParticipationDecision(
                topic_id=topic_id,
                agent_id=agent.id,
                action="reply",
                reason=f"replied to {primary.author_name}",
                post_id=post.id,
            )

        # initiative post
        post = await topic_plugin.add_agent_post(topic_id, agent, content)
        membership.initiative_posts += 1
        membership.unread_cursor = len(topic_plugin.get_posts(topic_id))
        return ParticipationDecision(
            topic_id=topic_id,
            agent_id=agent.id,
            action="post",
            reason="shared a new viewpoint",
            post_id=post.id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _in_cooldown(self, membership: TopicMembership) -> bool:
        if self._config.cooldown_seconds <= 0 or membership.last_post_at is None:
            return False
        elapsed = (_now() - membership.last_post_at).total_seconds()
        return elapsed < self._config.cooldown_seconds

    def _semaphore_for(self, topic_id: str) -> asyncio.Semaphore:
        if topic_id not in self._semaphores:
            self._semaphores[topic_id] = asyncio.Semaphore(
                self._config.max_concurrent_responses
            )
        return self._semaphores[topic_id]

    @staticmethod
    def _ignore(topic_id: str, agent_id: str, reason: str) -> ParticipationDecision:
        return ParticipationDecision(
            topic_id=topic_id,
            agent_id=agent_id,
            action="ignore",
            reason=reason,
        )

    def _require_app(self) -> "IdavollApp":
        if self._app is None:
            raise RuntimeError("TopicParticipationService is not installed")
        return self._app

    def _require_topic_plugin(self) -> TopicPlugin:
        if self._topic_plugin is None:
            raise RuntimeError(
                "TopicParticipationService requires TopicPlugin to be installed first"
            )
        return self._topic_plugin


# ---------------------------------------------------------------------------
# Attention queue helpers (module-level, pure functions — easy to test)
# ---------------------------------------------------------------------------


def _get_reply_depth(post: Post, posts_by_id: dict[str, Post]) -> int:
    """Count how many ancestors the post has in the reply chain."""
    depth = 0
    current = post
    while current.reply_to is not None and depth < 20:
        parent = posts_by_id.get(current.reply_to)
        if parent is None:
            break
        current = parent
        depth += 1
    return depth


def _build_attention_queue(
    all_posts: list[Post],
    agent: "Agent",
    unread_cursor: int,
    config: TopicConfig,
) -> list[Post]:
    """Return a prioritised list of candidate posts for the agent to consider.

    Priority order (lower value = higher priority):
        0  @mention of this agent
        1  direct reply to one of this agent's posts
        2  general unread post from someone else

    Posts beyond ``max_reply_depth`` are excluded.
    """
    posts_by_id: dict[str, Post] = {p.id: p for p in all_posts}
    agent_post_ids: set[str] = {p.id for p in all_posts if p.author_id == agent.id}
    unread = all_posts[unread_cursor:]

    candidates: list[tuple[int, Post]] = []
    for post in unread:
        if post.author_id == agent.id:
            continue  # skip own posts

        depth = _get_reply_depth(post, posts_by_id)
        if depth >= config.max_reply_depth:
            continue  # chain too deep

        mention = f"@{agent.name}" in post.content
        is_reply_to_agent = post.reply_to in agent_post_ids

        if mention:
            priority = 0
        elif is_reply_to_agent:
            priority = 1
        else:
            priority = 2

        candidates.append((priority, post))

    # Sort by priority then by original order (stable sort preserves recency).
    candidates.sort(key=lambda t: t[0])
    return [p for _, p in candidates[:3]]  # top 3


def _build_decision_instruction(
    topic: Topic,
    candidates: list[Post],
    can_reply: bool,
    can_post: bool,
) -> str:
    """Build the instruction that asks the agent to decide and generate content."""
    lines: list[str] = [f"你正在参与话题「{topic.title}」。"]

    if candidates and can_reply:
        lines.append("\n以下是需要你关注的新消息（按优先级排列）：")
        for i, post in enumerate(candidates, 1):
            excerpt = post.content[:200].replace("\n", " ")
            lines.append(f"  [{i}] {post.author_name}: {excerpt}")
        lines.append(
            "\n如果你想回应，请直接写出你的回复（将自动关联到最相关的消息）。"
        )
    elif can_post:
        lines.append("\n目前没有特别需要你回应的消息。")
        lines.append("如果你有新的想法或观点想分享，请直接写出来。")

    lines.append(
        "如果你认为当前不需要参与，只需回复「不参与」。"
        "根据你的人格和对话题的兴趣程度做出自然的判断。"
    )
    return "\n".join(lines)

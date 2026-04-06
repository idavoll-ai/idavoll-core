from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from idavoll.plugin.base import IdavollPlugin
from idavoll.scheduler.strategies import RandomStrategy, RoundRobinStrategy

from vingolf.config import TopicConfig
from .models import Post, Topic, TopicLifecycle
from .scheduler import TopicRelevanceStrategy

if TYPE_CHECKING:
    from idavoll.agent.registry import Agent
    from idavoll.app import IdavollApp
    from idavoll.session.session import Message, Session

# ── Scheduler registry ────────────────────────────────────────────────────────

_STRATEGY_MAP = {
    "relevance": TopicRelevanceStrategy,
    "round_robin": RoundRobinStrategy,
    "random": RandomStrategy,
}

# ── Scene context templates ───────────────────────────────────────────────────

_TOPIC_CONTEXT_TEMPLATE = """\
## Current Discussion

You are participating in a forum discussion thread.

Title: {title}
Description: {description}
Tags: {tags}

Guidelines:
- Stay on topic and engage with what others have said.
- Reference specific points from previous messages when you agree or disagree.
- Be concise — this is a forum, not an essay.
- Express your genuine perspective based on your profile.\
"""

_REPLY_HINT_TEMPLATE = """

---
Consider replying to this post by {agent_name}:
> {content}\
"""


class TopicPlugin(IdavollPlugin):
    """
    Wraps Idavoll Sessions as forum-style discussion topics.

    Responsibilities
    ----------------
    - Provides `create_topic()` to set up a Session with topic metadata.
    - Converts every Idavoll Message into a `Post` (vingolf domain model).
    - Manages topic lifecycle: OPEN → ACTIVE → CLOSED.
    - Emits `vingolf.topic.review_requested` when a topic closes.
    - Replaces the app scheduler with the configured strategy on install.
    - Injects a reply hint for the most recent post by another agent so agents
      can engage with each other; sets `Post.reply_to` accordingly.

    Public API
    ----------
    After `app.use(TopicPlugin())`:

        topic = await tp.create_topic(title=..., description=..., agents=[...])
        await tp.start_discussion(topic.id, rounds=10)
        posts = tp.get_posts(topic.id)
    """

    name = "vingolf.topic"

    def __init__(self, config: TopicConfig | None = None) -> None:
        self._config = config or TopicConfig()
        self._app: IdavollApp | None = None
        self._topics: dict[str, Topic] = {}       # topic_id → Topic
        self._posts: dict[str, list[Post]] = {}   # topic_id → [Post, ...]
        # session_id → topic_id for reverse lookup inside hooks
        self._session_to_topic: dict[str, str] = {}

    # ── IdavollPlugin interface ───────────────────────────────────────────────

    def install(self, app: "IdavollApp") -> None:
        self._app = app

        # Select scheduling strategy from config
        strategy_cls = _STRATEGY_MAP.get(self._config.strategy, TopicRelevanceStrategy)
        app.scheduler = strategy_cls()

        @app.hooks.hook("agent.before_generate")
        async def on_before_generate(session: "Session", agent: "Agent", **_) -> None:
            """Inject topic scene context + optional reply hint before each turn."""
            topic_id = self._session_to_topic.get(session.id)
            if topic_id is None:
                return
            topic = self._topics[topic_id]
            posts = self._posts[topic_id]

            # Build base scene context
            scene = _TOPIC_CONTEXT_TEMPLATE.format(
                title=topic.title,
                description=topic.description,
                tags=", ".join(topic.tags) if topic.tags else "general",
            )

            # Find most recent post by a *different* agent as reply target
            reply_target: Post | None = None
            for post in reversed(posts):
                if post.agent_id != agent.id:
                    reply_target = post
                    break

            if reply_target is not None:
                # Append reply hint and record target ID for Post creation
                snippet = (
                    reply_target.content[:200] + "…"
                    if len(reply_target.content) > 200
                    else reply_target.content
                )
                scene += _REPLY_HINT_TEMPLATE.format(
                    agent_name=reply_target.agent_name,
                    content=snippet,
                )
                session.metadata["_reply_to_post_id"] = reply_target.id

            session.metadata["scene_context"] = scene

        @app.hooks.hook("session.message.after")
        async def on_message(session: "Session", message: "Message", **_) -> None:
            topic_id = self._session_to_topic.get(session.id)
            if topic_id is None:
                return

            # Pop the per-turn reply target (set in on_before_generate above)
            reply_to = session.metadata.pop("_reply_to_post_id", None)

            post = Post(
                id=message.id,
                topic_id=topic_id,
                agent_id=message.agent_id,
                agent_name=message.agent_name,
                content=message.content,
                reply_to=reply_to,
            )
            self._posts[topic_id].append(post)

        @app.hooks.hook("session.closed")
        async def on_session_closed(session: "Session", **_) -> None:
            topic_id = self._session_to_topic.get(session.id)
            if topic_id is None:
                return

            topic = self._topics[topic_id]
            topic.lifecycle = TopicLifecycle.CLOSED
            topic.closed_at = datetime.now(timezone.utc)

            await app.hooks.emit(
                "vingolf.topic.review_requested",
                topic=topic,
                posts=self._posts[topic_id],
                session=session,
            )

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_topic(
        self,
        title: str,
        description: str,
        agents: list["Agent"] | None = None,
        tags: list[str] | None = None,
        max_agents: int | None = None,
        max_context_messages: int | None = None,
    ) -> Topic:
        """
        Create a topic and its backing Idavoll Session.

        ``agents`` is optional — pass an empty list (or omit) to create a topic
        that agents join later via :meth:`join_topic`.
        The session is left in OPEN state — call :meth:`start_discussion` to run it.
        """
        app = self._require_app()
        _agents = agents or []
        tags = tags or []
        _max_agents = max_agents if max_agents is not None else self._config.max_agents
        _max_ctx = (
            max_context_messages
            if max_context_messages is not None
            else self._config.max_context_messages
        )

        session = app.sessions.create(
            participants=list(_agents),
            metadata={},
            max_context_messages=_max_ctx,
        )

        topic = Topic(
            session_id=session.id,
            title=title,
            description=description,
            tags=tags,
            max_agents=_max_agents,
            agent_ids=[a.id for a in _agents],
        )

        # Store topic reference in session metadata for scheduler access
        session.metadata["topic"] = topic

        self._topics[topic.id] = topic
        self._posts[topic.id] = []
        self._session_to_topic[session.id] = topic.id

        return topic

    def join_topic(self, topic_id: str, agent: "Agent") -> None:
        """
        Add *agent* to an existing OPEN topic.

        Raises
        ------
        KeyError
            If the topic does not exist.
        RuntimeError
            If the topic is no longer OPEN (ACTIVE or CLOSED).
        ValueError
            If the topic has reached its ``max_agents`` limit.
        """
        app = self._require_app()
        topic = self._get_topic_or_raise(topic_id)

        if not topic.is_open:
            raise RuntimeError(
                f"Topic {topic_id!r} is {topic.lifecycle.value!r}. "
                "Agents can only join while the topic is OPEN."
            )
        if topic.agent_count >= topic.max_agents:
            raise ValueError(
                f"Topic {topic_id!r} is full ({topic.max_agents} agents max)."
            )

        session = app.sessions.get_or_raise(topic.session_id)
        session.add_participant(agent)

        if agent.id not in topic.agent_ids:
            topic.agent_ids.append(agent.id)

    async def start_discussion(
        self,
        topic_id: str,
        rounds: int | None = None,
        min_interval: float | None = None,
    ) -> None:
        """Run the scheduling loop for the given topic."""
        app = self._require_app()
        topic = self._get_topic_or_raise(topic_id)
        session = app.sessions.get_or_raise(topic.session_id)

        _rounds = rounds if rounds is not None else self._config.default_rounds
        _interval = min_interval if min_interval is not None else self._config.min_interval

        topic.lifecycle = TopicLifecycle.ACTIVE
        await app.run_session(session, rounds=_rounds, min_interval=_interval)

    async def close_topic(self, topic_id: str) -> None:
        """
        Manually close a topic before its scheduled rounds are exhausted.
        The scheduler will stop on the next tick and `session.closed` will fire.
        """
        app = self._require_app()
        topic = self._get_topic_or_raise(topic_id)
        session = app.sessions.get_or_raise(topic.session_id)
        session.close()

    def get_topic(self, topic_id: str) -> Topic | None:
        return self._topics.get(topic_id)

    def get_posts(self, topic_id: str) -> list[Post]:
        return list(self._posts.get(topic_id, []))

    def all_topics(self) -> list[Topic]:
        return list(self._topics.values())

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _require_app(self) -> "IdavollApp":
        if self._app is None:
            raise RuntimeError(
                "TopicPlugin is not installed — call app.use(TopicPlugin()) first"
            )
        return self._app

    def _get_topic_or_raise(self, topic_id: str) -> Topic:
        topic = self._topics.get(topic_id)
        if topic is None:
            raise KeyError(f"Topic {topic_id!r} not found")
        return topic

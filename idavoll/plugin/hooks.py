from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable

# Canonical hook names emitted by Idavoll Core.
# Plugins may define and emit additional hooks via app.hooks.emit().
CORE_HOOKS = frozenset(
    {
        "agent.created",
        "agent.profile.compiled",
        "session.created",
        "session.closed",
        "session.message.before",
        "session.message.after",
        "scheduler.selected",
        # Fired after scheduler picks the next agent, before PromptBuilder runs.
        # Prefer the two finer-grained hooks below for new code.
        "agent.before_generate",
        "agent.after_generate",
        # Forum-level hook: fired once per turn before any per-agent setup.
        # Handlers receive (session, agent) and should write shared context
        # into session.metadata (e.g. topic description, debate rules).
        "forum.before_turn",
        "forum.after_turn",
        # Seat-level hook: fired per turn, per agent, after forum.before_turn.
        # Handlers receive (seat, session, agent) and should write per-agent
        # context into seat.local_context (e.g. _memory_context, reply hints).
        "seat.before_generate",
        "seat.after_generate",
        # Fired after each LLM call completes. Payload: agent, session,
        # latency_ms (float), content_length (int).
        "llm.generate.before",
        "llm.generate.after",
        # Agent lifecycle within a session. Payload: session, agent.
        # "joined"  — agent was added mid-session (or re-joined after leaving).
        # "left"    — agent permanently left; seat state → LEFT.
        # "paused"  — agent temporarily suspended; seat state → PAUSED.
        # "resumed" — paused agent re-activated; seat state → ACTIVE.
        "session.agent.joined",
        "session.agent.left",
        "session.agent.paused",
        "session.agent.resumed",
    }
)


class HookBus:
    """
    Lightweight async event bus.

    Handlers are called concurrently (asyncio.gather) within each emit().
    Both async and sync callables are supported.

    Usage:
        # Register
        bus.on("session.message.after", my_handler)

        # Decorator form
        @bus.hook("session.closed")
        async def on_close(session, **_):
            ...

        # Emit
        await bus.emit("session.message.after", session=s, message=m)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable) -> None:
        try:
            self._handlers[event].remove(handler)
        except ValueError:
            pass

    def hook(self, event: str) -> Callable:
        """Decorator that registers the decorated function as a handler."""

        def decorator(fn: Callable) -> Callable:
            self.on(event, fn)
            return fn

        return decorator

    async def emit(self, event: str, **ctx: Any) -> None:
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            return

        async def _call(h: Callable) -> None:
            if asyncio.iscoroutinefunction(h):
                await h(**ctx)
            else:
                h(**ctx)

        await asyncio.gather(*(_call(h) for h in handlers))

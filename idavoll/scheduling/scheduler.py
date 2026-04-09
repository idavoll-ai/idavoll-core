"""Idavoll Scheduler — async job dispatching with cooldown and concurrency control.

Design (§4.2 mvp_design.md)
-----------------------------
The scheduler is a Core component.  It knows *when* to run a job but never
understands business semantics (topic IDs, review strategies, etc.).

Responsibilities
----------------
- **Concurrency cap** — a semaphore limits how many jobs run at the same time,
  protecting downstream services (LLM, DB) from overload.
- **Per-agent cooldown** — optional minimum spacing between dispatches for the
  same agent; callers that supply ``cooldown_seconds`` get a ``CooldownError``
  if the agent is still in cooldown rather than stacking work silently.
- **Background dispatch** — fire-and-forget tasks that do not block the caller
  (post-session growth, memory writes, review jobs).
- **Delayed dispatch** — run a coroutine after a fixed delay without blocking
  the caller.
- **Periodic tasks** — repeating background jobs (e.g., waking up product-layer
  participation checks on a fixed interval); cancellable via the returned Task.

What the scheduler does NOT do
--------------------------------
- It does not decide *whether* an agent should participate in a topic — that
  is the product layer's business logic (TopicParticipationService).
- It does not throttle external API calls — that belongs to LLMAdapter retries.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CooldownError(RuntimeError):
    """Raised when an agent is dispatched before its cooldown expires.

    Attributes
    ----------
    agent_id:
        The agent that triggered the cooldown.
    remaining:
        Approximate seconds left in the cooldown window.
    """

    def __init__(self, agent_id: str, remaining: float) -> None:
        self.agent_id = agent_id
        self.remaining = remaining
        super().__init__(
            f"Agent {agent_id!r} is in cooldown — {remaining:.1f}s remaining."
        )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class Scheduler:
    """Async job dispatcher with concurrency control, cooldown, and background support.

    Parameters
    ----------
    max_concurrent_jobs:
        Maximum number of coroutines that may run concurrently through
        ``dispatch()`` / ``dispatch_after()``.  Does not apply to
        ``dispatch_background()``.
    default_cooldown_seconds:
        Default per-agent cooldown applied when callers do not supply an
        explicit ``cooldown_seconds`` argument.  Pass ``0`` (default) to
        make cooldown opt-in.
    """

    def __init__(
        self,
        max_concurrent_jobs: int = 16,
        default_cooldown_seconds: float = 0.0,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._default_cooldown = default_cooldown_seconds
        # agent_id → UTC timestamp of last successful dispatch
        self._last_dispatch: dict[str, datetime] = {}
        # active background and periodic tasks (kept alive to avoid GC)
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # Foreground dispatch (awaitable)
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        agent_id: str | None = None,
        cooldown_seconds: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Await *job* under the concurrency semaphore.

        Parameters
        ----------
        job:
            An async callable to run.
        *args / **kwargs:
            Forwarded to *job*.
        agent_id:
            When supplied, per-agent cooldown is checked and the dispatch
            timestamp is recorded after a successful run.
        cooldown_seconds:
            Override the scheduler-level default cooldown for this call.
            Pass ``0`` to disable cooldown for this specific dispatch.
        """
        effective_cooldown = (
            cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
        )
        if agent_id and effective_cooldown > 0:
            self._enforce_cooldown(agent_id, effective_cooldown)

        async with self._semaphore:
            try:
                result = await job(*args, **kwargs)
            finally:
                if agent_id:
                    self._last_dispatch[agent_id] = datetime.now(timezone.utc)
            return result

    async def dispatch_after(
        self,
        delay: float,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        agent_id: str | None = None,
        cooldown_seconds: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Wait *delay* seconds, then dispatch *job* (awaits the result).

        Useful for implementing response delay without blocking the event loop.
        """
        if delay > 0:
            await asyncio.sleep(delay)
        return await self.dispatch(
            job, *args, agent_id=agent_id, cooldown_seconds=cooldown_seconds, **kwargs
        )

    # ------------------------------------------------------------------
    # Background dispatch (fire-and-forget)
    # ------------------------------------------------------------------

    def dispatch_background(
        self,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        agent_id: str | None = None,
        label: str = "",
        **kwargs: Any,
    ) -> "asyncio.Task[Any]":
        """Schedule *job* as a background task — returns immediately.

        The caller does not wait for the result.  Exceptions are logged but do
        not propagate.  Typical uses: post-session growth, memory sync, review.

        Parameters
        ----------
        label:
            Human-readable name attached to the Task for debugging.
        """
        name = label or (job.__name__ if hasattr(job, "__name__") else "background-job")
        task = asyncio.create_task(
            self._guarded(job, *args, agent_id=agent_id, **kwargs),
            name=name,
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------
    # Periodic scheduling
    # ------------------------------------------------------------------

    def schedule_periodic(
        self,
        interval: float,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        label: str = "",
        **kwargs: Any,
    ) -> "asyncio.Task[Any]":
        """Run *job* every *interval* seconds in the background.

        Returns the underlying Task; cancel it to stop the periodic execution::

            task = scheduler.schedule_periodic(30, check_topics)
            ...
            task.cancel()

        The first invocation happens after the first *interval* elapses, not
        immediately.  Exceptions inside *job* are logged and the loop continues.
        """
        name = label or (
            f"periodic:{job.__name__}" if hasattr(job, "__name__") else "periodic-job"
        )
        task = asyncio.create_task(
            self._periodic_loop(interval, job, *args, **kwargs),
            name=name,
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------
    # Cooldown queries
    # ------------------------------------------------------------------

    def cooldown_remaining(
        self, agent_id: str, cooldown_seconds: float | None = None
    ) -> float:
        """Return how many seconds the agent must still wait, or 0 if ready."""
        effective = (
            cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
        )
        if effective <= 0:
            return 0.0
        last = self._last_dispatch.get(agent_id)
        if last is None:
            return 0.0
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return max(0.0, effective - elapsed)

    def is_ready(self, agent_id: str, cooldown_seconds: float | None = None) -> bool:
        """Return True if the agent is not in cooldown."""
        return self.cooldown_remaining(agent_id, cooldown_seconds) == 0.0

    def reset_cooldown(self, agent_id: str) -> None:
        """Manually clear the cooldown for an agent (e.g. after a topic closes)."""
        self._last_dispatch.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def wait_for_background(self, timeout: float | None = None) -> None:
        """Wait until all background tasks have completed.

        Useful in tests and graceful shutdown sequences.  If *timeout* is
        given and expires, remaining tasks are left running (no cancellation).
        """
        if not self._tasks:
            return
        pending = list(self._tasks)
        done, _ = await asyncio.wait(pending, timeout=timeout)
        if len(done) < len(pending):
            logger.warning(
                "Scheduler.wait_for_background: %d task(s) still running after timeout",
                len(pending) - len(done),
            )

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel all pending background tasks and wait for them to finish."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enforce_cooldown(self, agent_id: str, cooldown_seconds: float) -> None:
        remaining = self.cooldown_remaining(agent_id, cooldown_seconds)
        if remaining > 0:
            raise CooldownError(agent_id, remaining)

    async def _guarded(
        self,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        agent_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run *job* and log any exception without re-raising it."""
        try:
            result = await job(*args, **kwargs)
            if agent_id:
                self._last_dispatch[agent_id] = datetime.now(timezone.utc)
            return result
        except asyncio.CancelledError:
            raise  # let cancellation propagate
        except Exception:
            label = job.__name__ if hasattr(job, "__name__") else repr(job)
            logger.exception("Background job %r raised an exception", label)
            return None

    async def _periodic_loop(
        self,
        interval: float,
        job: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        label = job.__name__ if hasattr(job, "__name__") else repr(job)
        while True:
            await asyncio.sleep(interval)
            try:
                await job(*args, **kwargs)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Periodic job %r raised an exception", label)

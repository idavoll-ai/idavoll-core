from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session.session import Session
    from .consolidator import MemoryConsolidator
    from .registry import Agent
    from .repository import AgentRepository

logger = logging.getLogger(__name__)


@dataclass
class _MemoryTask:
    agent: "Agent"
    session: "Session"


class MemoryWriteQueue:
    """
    A simple asyncio queue that consolidates and persists agent memory
    one task at a time in the background.

    Each (agent, session) pair is an independent task — a failure for one
    agent does not block or cancel others.

    Usage::

        queue = MemoryWriteQueue(consolidator, repo)
        queue.start()                          # launch background worker

        await queue.enqueue(agent, session)    # called from session.closed hook

        await queue.flush()                    # wait until all pending tasks finish
        await queue.stop()                     # graceful shutdown
    """

    def __init__(
        self,
        consolidator: "MemoryConsolidator",
        repo: "AgentRepository",
    ) -> None:
        self._consolidator = consolidator
        self._repo = repo
        self._queue: asyncio.Queue[_MemoryTask | None] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker. Call once after the event loop is running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._worker())

    async def stop(self) -> None:
        """Drain the queue then stop the worker."""
        await self._queue.put(None)  # sentinel
        if self._worker_task:
            await self._worker_task
            self._worker_task = None

    async def flush(self) -> None:
        """Block until every queued task has been processed."""
        await self._queue.join()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def enqueue(self, agent: "Agent", session: "Session") -> None:
        """Add a consolidate+save task for *agent* to the queue."""
        self._ensure_started()
        await self._queue.put(_MemoryTask(agent=agent, session=session))
        logger.debug("memory_queue: enqueued %s (session=%s)", agent.profile.name, session.id)

    # ── Worker ─────────────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:  # stop sentinel
                self._queue.task_done()
                break
            await self._process(item)
            self._queue.task_done()

    async def _process(self, task: _MemoryTask) -> None:
        agent = task.agent
        name = agent.profile.name
        try:
            if agent.profile.memory_plan.categories:
                await self._consolidator.consolidate(agent, task.session)
                logger.debug("memory_queue: consolidated %s", name)
            self._repo.save(agent)
            logger.info("memory_queue: saved memory for %s", name)
        except Exception:
            logger.exception("memory_queue: failed to process memory for %s", name)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _ensure_started(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self.start()

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable


class HookBus:
    """Small async event bus shared by core and product modules."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._handlers[event].append(handler)

    def hook(self, event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.on(event, fn)
            return fn

        return decorator

    async def emit(self, event: str, **ctx: Any) -> None:
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            return

        async def _call(handler: Callable[..., Any]) -> None:
            if asyncio.iscoroutinefunction(handler):
                await handler(**ctx)
            else:
                handler(**ctx)

        await asyncio.gather(*(_call(handler) for handler in handlers))

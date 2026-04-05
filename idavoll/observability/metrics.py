"""Simple in-memory metrics collector for the Idavoll framework."""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Histogram:
    values: list[float] = field(default_factory=list)

    def record(self, value: float) -> None:
        self.values.append(value)

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
        s = sorted(self.values)
        n = len(s)
        return {
            "count": n,
            "min": s[0],
            "max": s[-1],
            "mean": round(statistics.mean(s), 3),
            "p50": s[max(0, int(n * 0.50) - 1)],
            "p95": s[max(0, int(n * 0.95) - 1)],
        }


class MetricsCollector:
    """
    Lightweight in-memory metrics for the Idavoll framework.

    Tracks counters and histograms that can be queried programmatically
    at any time via :meth:`snapshot`.

    Counters:
        - ``sessions.total``
        - ``sessions.closed``
        - ``messages.total``
        - ``messages.total_chars``
        - ``llm.calls``
        - ``agents.created``
        - ``scheduler.selections.<agent_name>``
        - ``llm.calls_by_agent.<agent_name>``

    Histograms:
        - ``session.duration_s``
        - ``llm.latency_ms``
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._histograms: dict[str, _Histogram] = defaultdict(_Histogram)

    def increment(self, name: str, by: int = 1) -> None:
        self._counters[name] += by

    def record(self, name: str, value: float) -> None:
        self._histograms[name].record(value)

    def counter(self, name: str) -> int:
        return self._counters[name]

    def histogram(self, name: str) -> dict[str, float]:
        return self._histograms[name].summary()

    def snapshot(self) -> dict[str, Any]:
        """Return all current metrics as a plain dict (safe to serialize)."""
        return {
            "counters": dict(self._counters),
            "histograms": {k: v.summary() for k, v in self._histograms.items()},
        }

    def reset(self) -> None:
        """Clear all collected metrics."""
        self._counters.clear()
        self._histograms.clear()

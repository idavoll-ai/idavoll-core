"""Structured JSON logging utilities for the Idavoll framework."""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Fields that are part of every LogRecord but not user-supplied context
_STDLIB_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "taskName",
        "message",
    }
)


class JSONFormatter(logging.Formatter):
    """
    Formats each log record as a single-line JSON object.

    The emitted object always contains ``timestamp``, ``level``,
    ``logger``, and ``message``.  Any ``extra`` fields passed to the
    logging call are merged into the top-level object, making structured
    queries trivial::

        logger.info("llm.generate.after", extra={"latency_ms": 312, "agent_name": "Alice"})
        # → {"timestamp": "...", "level": "INFO", "event": "llm.generate.after", ...}
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()

        data: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        for key, val in record.__dict__.items():
            if key not in _STDLIB_FIELDS:
                data[key] = val

        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(
    level: int = logging.INFO,
    json: bool = True,
    stream: Any = None,
) -> None:
    """
    Configure the ``idavoll`` logger with sensible defaults.

    Call this once at application startup before creating the app::

        from idavoll.observability import configure_logging
        configure_logging(level=logging.DEBUG)

    Args:
        level:  Minimum log level (default: INFO).
        json:   Emit JSON lines when True (default); plain text when False.
        stream: Output stream (default: sys.stderr).
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JSONFormatter() if json else logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    ))

    idavoll_logger = logging.getLogger("idavoll")
    idavoll_logger.setLevel(level)
    idavoll_logger.addHandler(handler)
    idavoll_logger.propagate = False

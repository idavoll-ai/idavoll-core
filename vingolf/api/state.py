"""Global VingolfApp singleton, initialised once during FastAPI lifespan."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vingolf.app import VingolfApp

_app: "VingolfApp | None" = None


def set_app(app: "VingolfApp") -> None:
    global _app
    _app = app


def get_app() -> "VingolfApp":
    if _app is None:
        raise RuntimeError("VingolfApp not initialised — lifespan not started")
    return _app

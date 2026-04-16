from __future__ import annotations


class IdavollPlugin:
    """Base class for plugins installed into the core app."""

    name = "plugin"

    def install(self, app) -> None:
        ...

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import IdavollApp


class IdavollPlugin(ABC):
    """
    Base class for all Idavoll plugins.

    A plugin receives the fully-constructed IdavollApp on install and can:
      - Register hooks via app.hooks.on(...)
      - Replace the scheduler strategy via app.scheduler = MyStrategy()
      - Access the agent registry via app.agents
      - Access the session manager via app.sessions

    The framework calls install() once during app.use(plugin).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier, e.g. 'vingolf.topic'."""
        ...

    @abstractmethod
    def install(self, app: "IdavollApp") -> None:
        ...

    def __repr__(self) -> str:
        return f"Plugin({self.name!r})"

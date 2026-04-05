from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .memory import AgentMemory
from .profile import AgentProfile

if TYPE_CHECKING:
    from .registry import Agent


class AgentRepository:
    """
    Loads and saves agent configurations to YAML files.

    Each agent gets its own ``{name}.yaml`` under ``base_dir``:

    .. code-block:: yaml

        profile:
          id: "..."
          name: "退休物理教授"
          identity:
            role: "..."
            backstory: "..."
            goal: "..."
          voice:
            tone: casual
            quirks: [...]
            language: zh-CN
            example_messages:
              - input: "..."
                output: "..."
          budget:
            total: 4096
            reserved_for_output: 512
            scene_context_max: 300
          memory_plan:
            categories:
              - name: core_beliefs
                description: "经过讨论后我形成或加深的核心观点"
                max_entries: 10

        memory:
          entries:
            core_beliefs:
              - content: "..."
                formed_at: "2024-01-15"
                session_id: "..."
    """

    def __init__(self, base_dir: str | Path = "./agents") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, agent: "Agent") -> Path:
        """Persist agent profile + memory to its YAML file. Returns the path."""
        path = self._path_for(agent.profile.name)
        data = {
            "profile": agent.profile.model_dump(),
            "memory": agent.memory.model_dump(),
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return path

    def load(self, path: str | Path) -> tuple[AgentProfile, AgentMemory]:
        """Load a profile and its accumulated memory from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        profile = AgentProfile.model_validate(data["profile"])
        memory = AgentMemory.model_validate(data.get("memory") or {})
        return profile, memory

    def path_for_name(self, name: str) -> Path:
        return self._path_for(name)

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def all_paths(self) -> list[Path]:
        return sorted(self.base_dir.glob("*.yaml"))

    # ── Internal ───────────────────────────────────────────────────────────────

    def _path_for(self, name: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", name)
        return self.base_dir / f"{safe}.yaml"

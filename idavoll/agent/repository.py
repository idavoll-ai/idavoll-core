from __future__ import annotations

import json
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
    Loads and saves agent data with split storage:

    - Profile → ``{base_dir}/{name}.yaml``  (static configuration, version-controlled)
    - Memory  → ``{memory_dir}/{name}.json`` (accumulated runtime state, excluded from VCS)

    Profile YAML example::

        profile:
          id: "..."
          name: "退休物理教授"
          identity:
            role: "..."
          voice:
            tone: casual
          budget:
            total: 4096
          memory_plan:
            categories:
              - name: core_beliefs
                description: "经过讨论后我形成或加深的核心观点"
                max_entries: 10

    Memory JSON example (``data/memory/{name}.json``)::

        {
          "entries": {
            "core_beliefs": [
              {"content": "...", "formed_at": "2024-01-15", "session_id": "..."}
            ]
          }
        }
    """

    def __init__(
        self,
        base_dir: str | Path = "./data/agents",
        memory_dir: str | Path = "./data/memory",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.memory_dir = Path(memory_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, agent: "Agent") -> Path:
        """Persist agent profile to YAML and memory to JSON. Returns the YAML path."""
        yaml_path = self._path_for(agent.profile.name)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"profile": agent.profile.model_dump()},
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        mem_path = self._memory_path_for(agent.profile.name)
        with open(mem_path, "w", encoding="utf-8") as f:
            json.dump(agent.memory.model_dump(), f, ensure_ascii=False, indent=2)
        return yaml_path

    def load(self, path: str | Path) -> tuple[AgentProfile, AgentMemory]:
        """Load profile from YAML and memory from the corresponding JSON file (if present)."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Support old YAML files that still embed memory
        raw_profile = data["profile"] if "profile" in data else data
        profile = AgentProfile.model_validate(raw_profile)

        mem_path = self._memory_path_for(profile.name)
        if mem_path.exists():
            with open(mem_path, encoding="utf-8") as f:
                memory = AgentMemory.model_validate(json.load(f))
        else:
            # Fall back to embedded memory for backwards compatibility
            memory = AgentMemory.model_validate(data.get("memory") or {})

        return profile, memory

    def path_for_name(self, name: str) -> Path:
        return self._path_for(name)

    def memory_path_for_name(self, name: str) -> Path:
        return self._memory_path_for(name)

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def all_paths(self) -> list[Path]:
        return sorted(self.base_dir.glob("*.yaml"))

    # ── Internal ───────────────────────────────────────────────────────────────

    def _safe_name(self, name: str) -> str:
        return re.sub(r"[^\w\-]", "_", name)

    def _path_for(self, name: str) -> Path:
        return self.base_dir / f"{self._safe_name(name)}.yaml"

    def _memory_path_for(self, name: str) -> Path:
        return self.memory_dir / f"{self._safe_name(name)}.json"

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .model import Skill, parse_skill, render_skill, to_kebab

if TYPE_CHECKING:
    from ..agent.workspace import ProfileWorkspace


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SkillsLibrary:
    """Manages the Agent's skills collection via the workspace semantic API.

    Each skill is stored as a SKILL.md document identified by its kebab-case
    name.  All filesystem details are hidden behind the workspace interface —
    SkillsLibrary never touches paths directly.

    Lifecycle methods
    -----------------
    create  — add a new skill; raises ``FileExistsError`` if name taken.
    patch   — update an existing skill's fields without full replacement.
    archive — mark a skill inactive (keeps the document; just sets status).
    get     — load a skill by name, or ``None`` if not found.
    list_active — all non-archived skills.
    build_index — compact markdown string for the static system prompt.
    """

    def __init__(self, workspace: "ProfileWorkspace") -> None:
        self._workspace = workspace

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        body: str = "",
        tags: list[str] | None = None,
    ) -> Skill:
        """Create a new skill.  Raises ``FileExistsError`` if name already exists."""
        name = to_kebab(name)
        if self._workspace.skill_exists(name):
            raise FileExistsError(
                f"Skill {name!r} already exists. Use patch() to update it."
            )
        now = _now_iso()
        skill = Skill(
            name=name,
            description=description,
            body=body,
            tags=list(tags or []),
            status="active",
            created_at=now,
            updated_at=now,
        )
        self._write(skill)
        return skill

    def patch(
        self,
        name: str,
        *,
        description: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
    ) -> Skill:
        """Partially update an existing skill.  Raises ``FileNotFoundError`` if absent."""
        skill = self._load_or_raise(name)
        if description is not None:
            skill.description = description
        if body is not None:
            skill.body = body
        if tags is not None:
            skill.tags = list(tags)
        skill.updated_at = _now_iso()
        self._write(skill)
        return skill

    def archive(self, name: str) -> Skill:
        """Set a skill's status to 'archived'.  Safe to call on already-archived skills."""
        skill = self._load_or_raise(name)
        skill.status = "archived"
        skill.updated_at = _now_iso()
        self._write(skill)
        return skill

    def get(self, name: str) -> Skill | None:
        """Return the named skill, or ``None`` if it does not exist."""
        name = to_kebab(name)
        if not self._workspace.skill_exists(name):
            return None
        return parse_skill(self._workspace.read_skill_doc(name), name=name)

    def list_active(self) -> list[Skill]:
        """Return all skills with ``status == 'active'``, sorted by name."""
        return [
            skill
            for skill in self._load_all()
            if skill.status == "active"
        ]

    def list_all(self) -> list[Skill]:
        """Return all skills regardless of status."""
        return self._load_all()

    # ------------------------------------------------------------------
    # Prompt index
    # ------------------------------------------------------------------

    def build_index(self) -> str:
        """Return a compact Skills Index block for the static system prompt.

        Only active skills are included.  Each line shows the skill name,
        description, and tags so the model can decide when to activate one.

        Example output::

            ## Skills Index

            - **socratic-method**: 用苏格拉底式提问引导讨论 [reasoning, pedagogy]
            - **policy-analysis**: 分析 AI 政策提案的结构化方法 [policy]
        """
        active = self.list_active()
        if not active:
            return ""

        lines: list[str] = []
        for skill in active:
            tag_str = f" [{', '.join(skill.tags)}]" if skill.tags else ""
            lines.append(f"- **{skill.name}**: {skill.description}{tag_str}")

        return "## Skills Index\n\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_all(self) -> list[Skill]:
        return [
            parse_skill(self._workspace.read_skill_doc(name), name=name)
            for name in self._workspace.list_skill_names()
        ]

    def _load_or_raise(self, name: str) -> Skill:
        name = to_kebab(name)
        if not self._workspace.skill_exists(name):
            raise FileNotFoundError(f"Skill {name!r} not found.")
        return parse_skill(self._workspace.read_skill_doc(name), name=name)

    def _write(self, skill: Skill) -> None:
        self._workspace.write_skill_doc(skill.name, render_skill(skill))

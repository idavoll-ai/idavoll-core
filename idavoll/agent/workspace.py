from __future__ import annotations

from pathlib import Path

from .profile import (
    AgentProfile,
    SoulParseError,
    SoulSpec,
    parse_soul_markdown,
)

# ---------------------------------------------------------------------------
# Default file templates
# ---------------------------------------------------------------------------

_SOUL_TEMPLATE = """\
# {name}

## Identity

- **Role**: {role}
- **Backstory**: {backstory}
- **Goal**: {goal}

## Voice

- **Tone**: {tone}
- **Language**: {language}
- **Quirks**: {quirks}
{examples_block}"""

_MEMORY_TEMPLATE = """\
# Memory

<!-- Durable facts about the agent and its world.
     Store: preferences, corrections, long-term conclusions.
     Do NOT store: task logs, temporary TODOs, step-by-step reasoning. -->
"""

_USER_TEMPLATE = """\
# User Profile

<!-- Long-term user preferences, background, and interaction patterns.
     Updated by the agent over time; frozen into system prompt at session start. -->
"""

_PROJECT_TEMPLATE = """\
# Project Context

<!-- Describe the project this agent operates in.
     Loaded once at session start and injected as a static block. -->
"""


# ---------------------------------------------------------------------------
# ProfileWorkspace
# ---------------------------------------------------------------------------


class ProfileWorkspace:
    """Filesystem workspace for a single Agent Profile.

    Directory layout::

        {root}/
          SOUL.md          = agent identity and voice
          MEMORY.md        = durable facts (frozen at session start)
          USER.md          = user profile (frozen at session start)
          PROJECT.md       = optional project context
          skills/          = reusable workflow skills
          sessions/        = historical session summaries and search index
    """

    SOUL_FILE = "SOUL.md"
    MEMORY_FILE = "MEMORY.md"
    USER_FILE = "USER.md"
    PROJECT_FILE = "PROJECT.md"
    SKILLS_DIR = "skills"
    SESSIONS_DIR = "sessions"

    def __init__(self, root: Path) -> None:
        self._root = root

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._root

    @property
    def soul_path(self) -> Path:
        return self._root / self.SOUL_FILE

    @property
    def memory_path(self) -> Path:
        return self._root / self.MEMORY_FILE

    @property
    def user_path(self) -> Path:
        return self._root / self.USER_FILE

    @property
    def project_path(self) -> Path:
        return self._root / self.PROJECT_FILE

    @property
    def skills_dir(self) -> Path:
        return self._root / self.SKILLS_DIR

    @property
    def sessions_dir(self) -> Path:
        return self._root / self.SESSIONS_DIR

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def read_soul(self) -> str:
        return self._read(self.soul_path)

    def read_soul_spec(self) -> SoulSpec:
        """Parse SOUL.md into a structured SoulSpec."""
        return parse_soul_markdown(self.read_soul())

    def read_memory(self) -> str:
        return self._read(self.memory_path)

    def read_user(self) -> str:
        return self._read(self.user_path)

    def read_project_context(self) -> str:
        """Return PROJECT.md content, or empty string if absent."""
        return self._read(self.project_path)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def write_memory(self, content: str) -> None:
        self._write(self.memory_path, content)

    def write_user(self, content: str) -> None:
        self._write(self.user_path, content)

    def write_soul(self, content: str) -> None:
        self._write(self.soul_path, content)

    def write_soul_spec(self, profile: AgentProfile, soul: SoulSpec) -> None:
        self.write_soul(ProfileWorkspaceManager.render_soul(profile, soul))

    # ------------------------------------------------------------------
    # Skills helpers
    # ------------------------------------------------------------------

    def list_skills(self) -> list[Path]:
        """Return paths to all SKILL.md files under skills/."""
        if not self.skills_dir.exists():
            return []
        return sorted(self.skills_dir.glob("*/SKILL.md"))

    def skill_path(self, skill_name: str) -> Path:
        return self.skills_dir / skill_name / "SKILL.md"

    # ------------------------------------------------------------------
    # Sessions helpers
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[Path]:
        """Return paths to all session summary files under sessions/."""
        if not self.sessions_dir.exists():
            return []
        return sorted(self.sessions_dir.glob("*.md"))

    def session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.md"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def __repr__(self) -> str:
        return f"ProfileWorkspace(root={self._root!r})"


# ---------------------------------------------------------------------------
# ProfileWorkspaceManager
# ---------------------------------------------------------------------------


class ProfileWorkspaceManager:
    """Creates, loads, and manages Profile Workspaces on disk.

    All workspaces live under a single ``base_dir``::

        {base_dir}/
          {profile_id}/   ← one workspace per agent
            SOUL.md
            MEMORY.md
            ...
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    @property
    def base_dir(self) -> Path:
        return self._base

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create(self, profile: AgentProfile, soul: SoulSpec) -> ProfileWorkspace:
        """Create a new workspace from an AgentProfile and return it.

        Raises ``FileExistsError`` if the workspace already exists.
        """
        ws_dir = self._base / profile.id
        if ws_dir.exists():
            raise FileExistsError(
                f"Workspace already exists for profile {profile.id!r}: {ws_dir}"
            )
        ws_dir.mkdir(parents=True, exist_ok=False)
        (ws_dir / ProfileWorkspace.SKILLS_DIR).mkdir()
        (ws_dir / ProfileWorkspace.SESSIONS_DIR).mkdir()

        ws = ProfileWorkspace(ws_dir)
        ws.write_soul(self.render_soul(profile, soul))
        ws.write_memory(_MEMORY_TEMPLATE)
        ws.write_user(_USER_TEMPLATE)

        return ws

    def load(self, profile_id: str) -> ProfileWorkspace:
        """Load an existing workspace.

        Raises ``FileNotFoundError`` if the workspace does not exist.
        """
        ws_dir = self._base / profile_id
        if not ws_dir.is_dir():
            raise FileNotFoundError(
                f"No workspace found for profile {profile_id!r}: {ws_dir}"
            )
        return ProfileWorkspace(ws_dir)

    def get_or_create(self, profile: AgentProfile, soul: SoulSpec) -> ProfileWorkspace:
        """Return existing workspace or create a new one."""
        ws_dir = self._base / profile.id
        if ws_dir.is_dir():
            return ProfileWorkspace(ws_dir)
        return self.create(profile, soul)

    def exists(self, profile_id: str) -> bool:
        return (self._base / profile_id).is_dir()

    def delete(self, profile_id: str) -> None:
        """Remove the workspace directory and all its contents."""
        import shutil

        ws_dir = self._base / profile_id
        if ws_dir.is_dir():
            shutil.rmtree(ws_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def render_soul(profile: AgentProfile, soul: SoulSpec) -> str:
        identity = soul.identity
        voice = soul.voice
        quirks = "、".join(voice.quirks) if voice.quirks else "none"
        examples_block = ProfileWorkspaceManager._render_examples_block(
            profile.name, voice.example_messages
        )
        return _SOUL_TEMPLATE.format(
            name=profile.name,
            role=identity.role or profile.description or f"{profile.name} Agent",
            backstory=identity.backstory or "Not yet defined.",
            goal=identity.goal or "Respond consistently with this persona.",
            tone=voice.tone,
            language=voice.language,
            quirks=quirks,
            examples_block=examples_block,
        )

    @staticmethod
    def _render_examples_block(name: str, examples) -> str:
        if not examples:
            return ""

        lines = ["", "## Examples", ""]
        for idx, example in enumerate(examples, start=1):
            lines.extend(
                [
                    f"### Example {idx}",
                    "",
                    f"- **Input**: {example.input}",
                    f"- **Output**: {example.output}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

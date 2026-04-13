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

    All public methods are *semantic* — callers deal in names and content
    strings, never in filesystem paths.  Path details stay private so the
    storage backend can be swapped out later without touching any consumer.
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
    # Root path (for debugging / manager internals only)
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Core document reads
    # ------------------------------------------------------------------

    def read_soul(self) -> str:
        return self._read(self._root / self.SOUL_FILE)

    def read_soul_spec(self) -> SoulSpec:
        """Parse SOUL.md into a structured SoulSpec."""
        return parse_soul_markdown(self.read_soul())

    def read_memory(self) -> str:
        return self._read(self._root / self.MEMORY_FILE)

    def read_user(self) -> str:
        return self._read(self._root / self.USER_FILE)

    def read_project_context(self) -> str:
        """Return PROJECT.md content, or empty string if absent."""
        return self._read(self._root / self.PROJECT_FILE)

    # ------------------------------------------------------------------
    # Core document writes
    # ------------------------------------------------------------------

    def write_memory(self, content: str) -> None:
        self._write(self._root / self.MEMORY_FILE, content)

    def write_user(self, content: str) -> None:
        self._write(self._root / self.USER_FILE, content)

    def write_soul(self, content: str) -> None:
        self._write(self._root / self.SOUL_FILE, content)

    def write_soul_spec(self, profile: AgentProfile, soul: SoulSpec) -> None:
        self.write_soul(ProfileWorkspaceManager.render_soul(profile, soul))

    # ------------------------------------------------------------------
    # Skills — semantic interface (no Path exposed)
    # ------------------------------------------------------------------

    def list_skill_names(self) -> list[str]:
        """Return all skill names (kebab-case), sorted alphabetically."""
        skills_dir = self._root / self.SKILLS_DIR
        if not skills_dir.exists():
            return []
        return sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md"))

    def skill_exists(self, name: str) -> bool:
        return (self._root / self.SKILLS_DIR / name / "SKILL.md").exists()

    def read_skill_doc(self, name: str) -> str:
        """Return raw SKILL.md content for *name*, or '' if absent."""
        return self._read(self._root / self.SKILLS_DIR / name / "SKILL.md")

    def write_skill_doc(self, name: str, text: str) -> None:
        """Write raw SKILL.md content for *name*.  Creates directories as needed."""
        self._write(self._root / self.SKILLS_DIR / name / "SKILL.md", text)

    # ------------------------------------------------------------------
    # Sessions — semantic interface (no Path exposed)
    # ------------------------------------------------------------------

    def list_session_ids(self) -> list[str]:
        """Return all session IDs (file stems), sorted newest-first."""
        sessions_dir = self._root / self.SESSIONS_DIR
        if not sessions_dir.exists():
            return []
        return sorted(
            (p.stem for p in sessions_dir.glob("*.md")),
            reverse=True,
        )

    def read_session_summary(self, session_id: str) -> str:
        """Return raw session summary content for *session_id*, or '' if absent."""
        return self._read(self._root / self.SESSIONS_DIR / f"{session_id}.md")

    def write_session_summary(self, session_id: str, text: str) -> None:
        """Write session summary content.  Creates sessions/ directory if needed."""
        self._write(self._root / self.SESSIONS_DIR / f"{session_id}.md", text)

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
    # Rendering
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

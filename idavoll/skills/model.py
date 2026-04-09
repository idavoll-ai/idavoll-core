from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    """A single reusable workflow skill stored as SKILL.md."""

    name: str                              # kebab-case directory name
    description: str                       # one-line summary (shown in Skills Index)
    body: str = ""                         # full markdown body
    tags: list[str] = field(default_factory=list)
    status: Literal["active", "archived"] = "active"
    created_at: str = ""
    updated_at: str = ""
    path: Path | None = field(default=None, compare=False, repr=False)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def to_kebab(name: str) -> str:
    """Normalise a skill name to a safe kebab-case directory name."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name.strip("-")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def render_skill(skill: Skill) -> str:
    """Serialise a Skill to SKILL.md text."""
    tags_str = ", ".join(skill.tags)
    lines = [
        "---",
        f"name: {skill.name}",
        f"description: {skill.description}",
        f"tags: {tags_str}",
        f"status: {skill.status}",
        f"created_at: {skill.created_at}",
        f"updated_at: {skill.updated_at}",
        "---",
        "",
        skill.body.strip(),
    ]
    return "\n".join(lines)


def parse_skill(text: str, path: Path | None = None) -> Skill:
    """Deserialise SKILL.md text into a Skill.  Tolerates missing fields."""
    meta, body = _split_frontmatter(text)

    raw_tags = meta.get("tags", "")
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    status_raw = meta.get("status", "active")
    status: Literal["active", "archived"] = (
        "archived" if status_raw == "archived" else "active"
    )

    return Skill(
        name=meta.get("name", path.parent.name if path else ""),
        description=meta.get("description", ""),
        body=body,
        tags=tags,
        status=status,
        created_at=meta.get("created_at", ""),
        updated_at=meta.get("updated_at", ""),
        path=path,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (metadata_dict, body_text) from a YAML-frontmatter markdown string."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    return meta, parts[2].strip()

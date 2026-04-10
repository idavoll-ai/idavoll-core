from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


@dataclass
class ToolSpec:
    """Metadata for a single registered tool.

    *fn* is the actual callable implementation.  It may be ``None`` when a
    tool is declared only for prompt-guidance purposes (no runtime dispatch).

    Parameters are stored as a JSON-Schema-compatible dict so callers that
    need to bind tools to an LLM can pass them through without an extra
    conversion step.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    fn: Callable[..., Any] | None = field(default=None, repr=False)
    tags: list[str] = field(default_factory=list)


@dataclass
class Toolset:
    """A named group of tools, optionally composing other toolsets.

    ``includes`` is a list of other toolset names whose tools are merged in
    *before* this toolset's own ``tools`` list (depth-first).  Cycles are
    silently ignored so toolsets can be composed freely.

    Example::

        Toolset(name="search_plus", includes=["search"], tools=["image_search"])

    resolves to: all tools from "search" followed by "image_search".
    """

    name: str
    tools: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    description: str = ""


class ToolRegistry:
    """Global, append-only store of all available ToolSpecs.

    Tools are keyed by their unique ``name``.  Re-registering a name
    silently replaces the previous entry so hot-reload scenarios work
    without special teardown.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Add or replace a tool in the registry."""
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> ToolSpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Tool {name!r} not found in registry")
        return spec

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


class ToolsetManager:
    """Manages toolset definitions and resolves the active tool list per agent.

    Design (§4.2 mvp_design.md)
    ----------------------------
    - ``define()`` registers a Toolset by name.
    - Toolsets may ``include`` other toolsets; resolution is depth-first and
      cycle-safe, so composite toolsets like ``"all"`` are safe to build.
    - ``resolve()`` expands the full list of ToolSpecs for a given
      ``enabled_toolsets`` + ``disabled_tools`` configuration.
    - ``build_index()`` renders that list as a compact prompt block for slot
      [8] of the static system prompt.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._toolsets: dict[str, Toolset] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def define(self, toolset: Toolset) -> None:
        """Register a toolset.  Re-defining a name replaces the previous entry."""
        self._toolsets[toolset.name] = toolset

    def get_toolset(self, name: str) -> Toolset | None:
        return self._toolsets.get(name)

    def all_toolset_names(self) -> list[str]:
        return list(self._toolsets.keys())

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        enabled_toolsets: list[str],
        *,
        disabled_tools: list[str] | None = None,
    ) -> list[ToolSpec]:
        """Return the ordered, deduplicated ToolSpec list for an agent.

        Steps:
        1. Expand each toolset in ``enabled_toolsets`` depth-first (includes
           before own tools).  Tool names are deduplicated while preserving
           first-seen order.
        2. Drop any tool whose name is in ``disabled_tools``.
        3. Look up each surviving name in the registry; unknown names are
           silently skipped so profile configs can be forward-compatible.
        """
        disabled: set[str] = set(disabled_tools or [])
        seen_toolsets: set[str] = set()
        ordered_names: list[str] = []

        def _expand(ts_name: str) -> None:
            if ts_name in seen_toolsets:
                return
            seen_toolsets.add(ts_name)
            ts = self._toolsets.get(ts_name)
            if ts is None:
                return
            for inc in ts.includes:
                _expand(inc)
            for tool_name in ts.tools:
                if tool_name not in ordered_names:
                    ordered_names.append(tool_name)

        for name in enabled_toolsets:
            _expand(name)

        return [
            spec
            for tool_name in ordered_names
            if tool_name not in disabled
            and (spec := self._registry.get(tool_name)) is not None
        ]

    def build_index(
        self,
        enabled_toolsets: list[str],
        *,
        disabled_tools: list[str] | None = None,
    ) -> str:
        """Render the resolved tool list as a prompt-ready block.

        Returns an empty string when no tools are available so the caller
        can skip injecting an empty section.
        """
        tools = self.resolve(enabled_toolsets, disabled_tools=disabled_tools)
        if not tools:
            return ""
        lines = ["## Available Tools"]
        for spec in tools:
            lines.append(f"- **{spec.name}**: {spec.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------

_TOOL_ATTR = "__tool_spec__"


def tool(
    name: str | None = None,
    *,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that marks a function as an auto-registerable tool.

    Usage::

        @tool(description="Search the web for a query.")
        def web_search(query: str) -> str:
            ...

        @tool()
        def my_tool(x: int) -> str:
            \"\"\"First docstring line becomes the description.\"\"\"
            ...

    The decorated function is returned unchanged; the ``ToolSpec`` is stored
    as ``fn.__tool_spec__`` so ``ToolRegistry.scan_module()`` can pick it up.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        resolved_name = name or fn.__name__
        resolved_desc = description
        if not resolved_desc:
            doc = (fn.__doc__ or "").strip()
            resolved_desc = doc.splitlines()[0] if doc else resolved_name
        setattr(
            fn,
            _TOOL_ATTR,
            ToolSpec(
                name=resolved_name,
                description=resolved_desc,
                parameters=parameters or {},
                fn=fn,
                tags=list(tags or []),
            ),
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Auto-scan helpers (added to ToolRegistry via monkey-style extension below)
# ---------------------------------------------------------------------------


def _scan_module(registry: ToolRegistry, module: ModuleType) -> int:
    """Register all @tool-decorated functions found in *module*.

    Returns the number of tools registered.
    """
    count = 0
    for _attr_name, obj in inspect.getmembers(module, inspect.isfunction):
        spec: ToolSpec | None = getattr(obj, _TOOL_ATTR, None)
        if spec is not None:
            registry.register(spec)
            count += 1
    return count


def _scan_package(registry: ToolRegistry, package: ModuleType | str | Path) -> int:
    """Recursively scan a package and register all @tool-decorated functions.

    *package* can be:
    - a module object (already imported)
    - a dotted package name string (e.g. ``"myapp.tools"``)
    - a filesystem ``Path`` pointing to a package directory

    Returns the total number of tools registered across all sub-modules.
    """
    if isinstance(package, Path):
        # Import by file path
        pkg_path = package
        pkg_name = pkg_path.name
        spec_obj = importlib.util.spec_from_file_location(
            pkg_name, pkg_path / "__init__.py"
        )
        if spec_obj is None or spec_obj.loader is None:
            raise ImportError(f"Cannot import package from path: {pkg_path}")
        mod = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(mod)  # type: ignore[union-attr]
        package = mod
    elif isinstance(package, str):
        package = importlib.import_module(package)

    total = _scan_module(registry, package)
    pkg_path_list = getattr(package, "__path__", None)
    if pkg_path_list is None:
        return total  # plain module, not a package

    for module_info in pkgutil.walk_packages(
        pkg_path_list, prefix=package.__name__ + "."
    ):
        try:
            sub = importlib.import_module(module_info.name)
        except ImportError:
            continue
        total += _scan_module(registry, sub)
    return total


# Attach scan helpers as methods on ToolRegistry
ToolRegistry.scan_module = _scan_module  # type: ignore[method-assign]
ToolRegistry.scan_package = _scan_package  # type: ignore[method-assign]

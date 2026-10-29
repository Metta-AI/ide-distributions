"""The tool catalog: which tools each role gets (design C.2) and the tool budget.

Tool modules register their ``ToolSpec`` here at import time; :func:`tools_for` returns the
role's set. The budget keeps every tool loaded in Hermes rather than deferred behind
``tool_search``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from research_core.tools.base import ToolSpec

Role = Literal["orchestrator", "researcher"]
ROLES: tuple[Role, ...] = ("orchestrator", "researcher")
TOOL_BUDGET = 25

TOOLS: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOLS:
        raise ValueError(f"tool {spec.name!r} registered twice")
    TOOLS[spec.name] = spec
    return spec


def tools_for(role: Role) -> list[ToolSpec]:
    _load_tool_modules()
    return sorted((spec for spec in TOOLS.values() if role in spec.roles), key=lambda s: s.name)


# Tool modules, imported on first use so that importing the catalog stays cheap.
TOOL_MODULES: tuple[str, ...] = ()
_loaded = False


def _load_tool_modules() -> None:
    global _loaded
    if _loaded:
        return
    import importlib

    for module_name in TOOL_MODULES:
        importlib.import_module(module_name)
    _loaded = True

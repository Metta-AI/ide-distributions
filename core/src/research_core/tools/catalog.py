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


# Tool modules, imported on first use so that importing the catalog stays cheap. A module that
# does not exist yet is skipped; the tool-budget test catches a missing tool.
TOOL_MODULES: tuple[str, ...] = (
    "research_core.tools.goal",
    "research_core.tools.task",
    "research_core.tools.claim",
    "research_core.tools.experiment",
    "research_core.tools.version",
    "research_core.tools.directive",
    "research_core.tools.settings",
    "research_core.tools.ui",
    "research_core.tools.workspace",
    "research_core.tools.upload",
    "research_core.tools.submit",
    "research_core.tools.xp",
    "research_core.tools.platform",
    "research_core.tools.forum_post",
    "research_core.tools.wiki_edit",
    "research_core.tools.softmax_api",
    "research_core.tools.github",
    "research_core.tools.attachment_download",
)
_loaded = False
# Modules that failed to import, by name: a broken tool module costs its own tools, never the
# others. workspace.doctor and the tool-budget test surface the entries.
LOAD_ERRORS: dict[str, str] = {}


def _load_tool_modules() -> None:
    global _loaded
    if _loaded:
        return
    import importlib

    for module_name in dict.fromkeys(TOOL_MODULES):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name and error.name.startswith("research_core.tools"):
                continue  # not built yet
            LOAD_ERRORS[module_name] = f"{type(error).__name__}: {error}"
        except Exception as error:  # a syntax error or a failing import
            LOAD_ERRORS[module_name] = f"{type(error).__name__}: {error}"
    _loaded = True

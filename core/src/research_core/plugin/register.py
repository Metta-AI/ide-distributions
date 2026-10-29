"""Build the ``register(ctx)`` function a profile's plugin shim exports.

Hermes calls ``register(ctx)`` once per process with a ``PluginContext``. Registration must be
quick (Hermes abandons a plugin after ten seconds) and must not import anything outside the
standard library, because it runs inside Hermes's own interpreter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_core.tools.catalog import Role, tools_for


def toolset_name(role: Role) -> str:
    return f"ide-{role}"


def make_register(role: Role) -> Callable[[Any], None]:
    def register(ctx: Any) -> None:
        from research_core.plugin.executor import make_handler

        for spec in tools_for(role):
            ctx.register_tool(
                name=spec.name,
                toolset=toolset_name(role),
                schema=spec.schema(role),
                handler=make_handler(spec, role),
                description=spec.description,
            )

    register.__name__ = f"register_{role}"
    return register

"""Build the ``register(ctx)`` function a profile's plugin shim exports.

Hermes calls ``register(ctx)`` once per process with a ``PluginContext``. Registration must be
quick (Hermes abandons a plugin after ten seconds) and must not import anything outside the
standard library, because it runs inside Hermes's own interpreter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_core.tools.catalog import Role, tools_for

# Two plugin sections share Hermes's 8,000-character budget, headings included (A3.3).
SECTION_MAX_CHARS = 3800


def toolset_name(role: Role) -> str:
    return f"ide-{role}"


def make_register(role: Role) -> Callable[[Any], None]:
    def register(ctx: Any) -> None:
        from research_core.plugin.advisory import make_hooks
        from research_core.plugin.executor import make_handler

        for spec in tools_for(role):
            ctx.register_tool(
                name=spec.name,
                toolset=toolset_name(role),
                schema=spec.schema(role),
                handler=make_handler(spec, role),
                description=spec.description,
            )
        for hook_name, callback in make_hooks(role).items():
            ctx.register_hook(hook_name, callback)
        from research_core.plugin.briefing import make_section as make_briefing_section

        ctx.register_system_prompt_section(
            "ide-briefing", make_briefing_section(role), max_chars=SECTION_MAX_CHARS
        )
        if role == "orchestrator":
            from research_core.plugin.session_context import make_section
            from research_core.plugin.turn import make_pre_llm_call

            ctx.register_hook("pre_llm_call", make_pre_llm_call(role))
            ctx.register_system_prompt_section(
                "ide-session-context", make_section(role), max_chars=SECTION_MAX_CHARS
            )

    register.__name__ = f"register_{role}"
    return register

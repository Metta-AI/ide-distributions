"""Test plugin for ``test_hermes_patches.py``: one JSON line per hook call in ``$RECORDER_OUT``.

Profile ``recording`` registers everything. Every other profile registers the tools, and only
``on_gateway_exit`` among the new hooks. Runs in Hermes's interpreter.
"""

import json
import os
import threading

NEW_HOOKS = (
    "on_system_prompt_built",
    "on_model_attempt",
    "on_tool_results_final",
    "on_messages_committed",
)
OBSERVED_EXISTING_HOOKS = (
    "pre_llm_call",
    "pre_api_request",
    "post_api_request",
    "post_tool_call",
    "pre_auxiliary_call",
    "post_auxiliary_call",
)
_write_lock = threading.Lock()


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)


def _recorder(profile, hook_name):
    def record(**kwargs):
        line = json.dumps({"hook": hook_name, "profile": profile, **kwargs}, default=_plain)
        with _write_lock, open(os.environ["RECORDER_OUT"], "a", encoding="utf-8") as out:
            out.write(line + "\n")

    return record


def _raises(**_):
    raise RuntimeError("a hook that raises must not reach the agent")


def _big_tool(args, **_):
    return "big"


def _small_tool(args, **_):
    return "small"


def _tool_schema(name):
    return {
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {}},
    }


def register(ctx):
    profile = ctx.profile_name
    ctx.register_tool(
        name="big_tool",
        toolset="recorder",
        schema=_tool_schema("big_tool"),
        handler=_big_tool,
    )
    ctx.register_tool(
        name="small_tool",
        toolset="recorder",
        schema=_tool_schema("small_tool"),
        handler=_small_tool,
    )
    ctx.register_hook("on_gateway_exit", _recorder(profile, "on_gateway_exit"))
    if profile != "recording":
        return
    for hook_name in (*NEW_HOOKS, *OBSERVED_EXISTING_HOOKS, "on_gateway_exit"):
        ctx.register_hook(hook_name, _raises)
    for hook_name in (*NEW_HOOKS, *OBSERVED_EXISTING_HOOKS):
        ctx.register_hook(hook_name, _recorder(profile, hook_name))

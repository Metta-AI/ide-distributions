"""Run a tool call either in the Hermes process or in the workspace environment.

Store-only tools run in process (``executor="inproc"``). Tools that need the Softmax client,
PyYAML or numpy run as ``<workspace venv>/bin/python -m research_core.cli tool <name>`` and
their stdout is the JSON result (``executor="workspace"``). Either way the handler Hermes sees
takes the model's ``args`` and the injected ``session_id`` and returns a JSON string, never
raising.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from research_core import layout
from research_core.tools.catalog import Role

TOOL_TIMEOUT_S = 400  # under Hermes's 420 s tool deadline


def profile_env(**overrides: str) -> dict[str, str]:
    """The environment the profile's code runs with: the process environment plus the routed
    profile's secret scope, plus ``overrides``.

    Under a multiplexing gateway Hermes keeps each profile's ``.env`` out of ``os.environ`` (so
    one profile's keys never leak into another's turns) and exposes the routed profile's values
    per task through ``agent.secret_scope``. Everything the plugin reads from the environment,
    in process or for a workspace subprocess, goes through here so a profile's ``.env`` reaches
    the tools the way the design assumes.
    """
    merged = dict(os.environ)
    merged.update(_secret_scope())
    merged.update(overrides)
    return merged


def _secret_scope() -> dict[str, str]:
    try:
        from agent.secret_scope import current_secret_scope
    except ImportError:  # outside Hermes: tests and the CLI
        return {}
    try:
        scope = current_secret_scope()
    except Exception:  # a scope API change must never take the tools down
        return {}
    return {str(k): str(v) for k, v in (scope or {}).items() if v is not None}


def tool_env(role: Role, session_id: str, env: dict[str, str] | None = None) -> dict[str, str]:
    merged = profile_env() if env is None else dict(env)
    merged["IDE_ROLE"] = role
    if session_id:
        merged["IDE_SESSION_ID"] = session_id
    return merged


def error_result(message: str, **context: Any) -> str:
    return json.dumps({"ok": False, "error": message, "problems": [message], "context": context})


def run_in_workspace(
    name: str, args: dict, env: dict[str, str], timeout_s: int | None = None
) -> str:
    ws = layout.from_env(env)
    python = ws.venv_python
    if not python.exists():
        return error_result(
            "workspace environment missing; run the profile's scripts/prepare.py",
            expected=str(python),
        )
    command = [str(python), "-m", "research_core.cli", "tool", name, "--args", json.dumps(args)]
    timeout = timeout_s or TOOL_TIMEOUT_S
    try:
        completed = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return error_result(f"tool {name} timed out after {timeout} s", command=command)
    stdout = completed.stdout.strip()
    if completed.returncode != 0 or not stdout:
        return error_result(
            f"tool {name} process failed",
            exit_code=completed.returncode,
            stdout_tail=stdout[-2000:],
            stderr_tail=completed.stderr[-2000:],
            command=command,
        )
    return stdout.splitlines()[-1]


def make_handler(spec: Any, role: Role) -> Callable[..., str]:
    def handler(args: dict | None = None, session_id: str = "", **_: Any) -> str:
        args = dict(args or {})
        env = tool_env(role, session_id)
        try:
            if spec.executor == "inproc":
                from research_core.tools.base import ToolContext, run_tool

                return run_tool(spec.name, args, ToolContext.from_env(role, env))
            return run_in_workspace(spec.name, args, env, spec.timeout_s)
        except Exception as error:  # never raise into Hermes
            return error_result(f"{type(error).__name__}: {error}", tool=spec.name)

    handler.__name__ = f"tool_{spec.name}"
    return handler

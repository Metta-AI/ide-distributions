"""The shape of a profile tool and the one entry point that runs it (design §4, C.2).

A tool is a :class:`ToolSpec`: a name, the roles that get it, where it executes, either a
flat parameter set or a list of actions with flat optional inputs, and a handler that takes a
:class:`ToolCall` and returns a JSON-ready dict. :func:`run_tool` validates the arguments
against the role's view of the tool, reports every problem at once, runs the handler and
renders the result as JSON text. It never raises: Hermes gets a string in every case.

Stdlib only: store-only tools run inside Hermes.
"""

from __future__ import annotations

import json
import os
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal

from research_core import layout
from research_core.store.actors import Actor
from research_core.store.errors import StoreError
from research_core.tools.catalog import TOOLS, Role, register

if TYPE_CHECKING:  # pragma: no cover
    from research_core.layout import Coworld, Workspace
    from research_core.store.db import Store

Executor = Literal["inproc", "workspace"]
JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolError(Exception):
    """A refusal the tool explains: every problem at once, plus context the agent can use."""

    def __init__(self, problems: list[str] | str, **context: Any):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        self.context = context
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    params: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    roles: frozenset[Role] = frozenset({"orchestrator", "researcher"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[[ToolCall], dict]
    roles: frozenset[Role] = frozenset({"orchestrator", "researcher"})
    executor: Executor = "inproc"
    actions: tuple[ActionSpec, ...] | None = None
    params: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    # Wall-clock budget of a workspace-executor call; None means the executor default (400 s,
    # under Hermes's 420 s tool deadline). Long mixin commands run detached instead (A3.2).
    timeout_s: int | None = None

    def actions_for(self, role: Role) -> list[ActionSpec]:
        return [action for action in self.actions or () if role in action.roles]

    def action(self, name: str) -> ActionSpec | None:
        for action in self.actions or ():
            if action.name == name:
                return action
        return None

    def properties(self, role: Role) -> dict[str, dict]:
        """The flat parameter set a role sees: common params plus every action's inputs."""
        properties: dict[str, dict] = {}
        if self.actions is not None:
            actions = self.actions_for(role)
            properties["action"] = {
                "type": "string",
                "enum": [action.name for action in actions],
                "description": " ".join(f"{a.name}: {a.description}" for a in actions),
            }
            for action in actions:
                for name, schema in action.params.items():
                    properties.setdefault(name, schema)
        for name, schema in self.params.items():
            properties.setdefault(name, schema)
        return properties

    def schema(self, role: Role) -> dict:
        """The OpenAI function body Hermes registers: name, description, parameters."""
        required = list(self.required)
        if self.actions is not None:
            required = ["action", *required]
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": self.properties(role),
            "additionalProperties": False,
        }
        if required:
            parameters["required"] = required
        return {"name": self.name, "description": self.description, "parameters": parameters}


def tool(spec: ToolSpec) -> ToolSpec:
    """Register a tool in the catalog (used as ``SPEC = tool(ToolSpec(...))``)."""
    return register(spec)


@dataclass
class ToolContext:
    role: Role
    actor: Actor
    workspace: Workspace
    env: Mapping[str, str]

    @classmethod
    def from_env(cls, role: Role, env: Mapping[str, str] | None = None) -> ToolContext:
        env = dict(os.environ if env is None else env)
        return cls(role=role, actor=Actor.from_env(env), workspace=layout.from_env(env), env=env)

    def hermes_session_id(self) -> str:
        return self.env.get("IDE_SESSION_ID") or self.env.get("HERMES_SESSION_ID") or ""

    @cached_property
    def wstore(self) -> Store:
        from research_core.store.db import Store

        return Store.open(self.workspace.store_path, kind="workspace")

    def coworld(self) -> Coworld:
        from research_core.store import coworlds

        active = coworlds.active(self.wstore)
        if active is None:
            raise ToolError(
                "no active Coworld; the Orchestrator selects one with workspace.coworld_switch"
            )
        return self.workspace.coworld(active["coworld_id"])

    def coworld_or_none(self) -> Coworld | None:
        try:
            return self.coworld()
        except ToolError:
            return None

    @cached_property
    def store(self) -> Store:
        from research_core.store.db import Store

        return Store.open(self.coworld().store_path, kind="coworld")

    def platform(self):
        from research_core.platform import from_env

        return from_env(self.env, self.workspace)

    def scrub(self, text: str) -> str:
        """Keep store and workspace paths out of what the agent sees (design B.10)."""
        return text.replace(str(self.workspace.root), "<workspace>")


@dataclass
class ToolCall:
    spec: ToolSpec
    args: dict
    ctx: ToolContext
    action: str | None = None

    def get(self, name: str, default: Any = None) -> Any:
        return self.args.get(name, default)


def validate_args(
    spec: ToolSpec, role: Role, args: Mapping[str, Any]
) -> tuple[str | None, list[str]]:
    """The selected action (if any) and every problem with the arguments."""
    problems: list[str] = []
    properties = spec.properties(role)
    action_name: str | None = None
    required = list(spec.required)
    if spec.actions is not None:
        allowed = [action.name for action in spec.actions_for(role)]
        raw = args.get("action")
        if raw is None:
            problems.append(f"action is required; one of {', '.join(allowed)}")
        elif raw not in allowed:
            known = spec.action(str(raw))
            if known is not None:
                problems.append(
                    f"action {raw!r} is not available to the {role}; allowed: {', '.join(allowed)}"
                )
            else:
                problems.append(f"unknown action {raw!r}; one of {', '.join(allowed)}")
        else:
            action_name = str(raw)
            required.extend(spec.action(action_name).required)  # type: ignore[union-attr]
    known = set(properties) | {name for action in spec.actions or () for name in action.params}
    for name in args:
        if name not in known:
            problems.append(f"unknown field {name!r}")
    for name in required:
        if args.get(name) in (None, ""):
            problems.append(f"{name} is required" + (f" for {action_name}" if action_name else ""))
    for name, value in args.items():
        schema = properties.get(name)
        if schema is None or value is None:
            continue
        expected = JSON_TYPES.get(schema.get("type", ""))
        if expected is str and isinstance(value, int) and not isinstance(value, bool):
            continue  # an integer id for a string reference field is coerced by coerce_args
        if expected is not None and not isinstance(value, expected):
            problems.append(f"{name} must be {schema['type']}")
        elif expected is int and isinstance(value, bool):
            problems.append(f"{name} must be integer")
        if "enum" in schema and value not in schema["enum"] and name != "action":
            problems.append(f"{name} must be one of {', '.join(map(str, schema['enum']))}")
    return action_name, problems


def coerce_args(spec: ToolSpec, role: Role, args: Mapping[str, Any]) -> dict:
    """Integers given for string fields (record ids) become strings; everything else is kept."""
    properties = spec.properties(role)
    out = dict(args)
    for name, value in args.items():
        schema = properties.get(name)
        if schema and schema.get("type") == "string" and isinstance(value, int):
            if not isinstance(value, bool):
                out[name] = str(value)
    return out


def error_payload(problems: list[str], context: dict | None = None) -> dict:
    return {
        "ok": False,
        "error": problems[0] if len(problems) == 1 else "; ".join(problems),
        "problems": problems,
        "context": context or {},
    }


def run_tool(name: str, args: Mapping[str, Any], ctx: ToolContext) -> str:
    """Validate, run and render a tool call as JSON text. Never raises."""
    from research_core.tools.catalog import _load_tool_modules

    _load_tool_modules()
    spec = TOOLS.get(name)
    if spec is None or ctx.role not in spec.roles:
        return json.dumps(error_payload([f"unknown tool {name!r} for the {ctx.role}"]))
    action, problems = validate_args(spec, ctx.role, args)
    if problems:
        return json.dumps(error_payload(problems, {"tool": name, "action": action}))
    try:
        call = ToolCall(spec=spec, args=coerce_args(spec, ctx.role, args), ctx=ctx, action=action)
        result = spec.handler(call)
        if not isinstance(result, dict):
            result = {"result": result}
        result.setdefault("ok", True)
        text = json.dumps(result, default=str)
    except ToolError as error:
        text = json.dumps(error_payload(error.problems, {"tool": name, **error.context}))
    except StoreError as error:
        text = json.dumps(
            error_payload(list(error.problems) or [str(error)], {"tool": name, "action": action})
        )
    except Exception as error:  # the agent must see what failed, with context
        text = json.dumps(
            error_payload(
                [f"{type(error).__name__}: {error}"],
                {"tool": name, "action": action, "traceback": traceback.format_exc()[-2000:]},
            )
        )
    return ctx.scrub(text)

"""Durable requests to the coordinator; tools carry intent, never scheduler authority.

The local function call applies one domain command and acknowledges it in the same transaction.
Cross-process delivery uses SQLite and bounded polling: no native runtime hook is required.
Stdlib only, because submit runs inside the pinned native interpreter.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from research_core.store import admissions
from research_core.store._common import parse_iso
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError
from research_core.tools.base import ToolCall, ToolContext, ToolError, ToolSpec, tool

REQUEST_WAIT_SECONDS = 15

CommandState = Literal["pending", "applied", "refused"]


@dataclass(frozen=True)
class CommandReceipt:
    state: CommandState
    payload: dict

    @classmethod
    def parse(cls, payload: dict) -> CommandReceipt:
        state = payload["transition_state"]
        if state not in {"pending", "applied", "refused"}:
            raise ValueError("unknown command receipt state")
        return cls(state, payload)


@dataclass
class CommandApplication:
    """Convert expected domain refusals after rollback into durable terminal results.

    Unexpected exceptions propagate. The inner atomic transaction must exit first.
    """

    store: Store
    command_id: str

    def __enter__(self) -> CommandApplication:
        return self

    def __exit__(self, exc_type, error, tb) -> bool:
        if isinstance(error, Exception) and not isinstance(error, (ToolError, StoreError)):
            row = self.store.one(
                "SELECT attempts FROM transition_commands WHERE id=?", (self.command_id,)
            )
            with self.store.tx(Actor.harness()) as tx:
                retry_at = (
                    parse_iso(tx.at) + timedelta(seconds=min(60, 2 ** min(row["attempts"], 6)))
                ).isoformat()
                detail = f"{type(error).__name__}: {error}"
                tx.execute(
                    "UPDATE transition_commands SET last_error=?,retry_at=? WHERE id=?",
                    (detail, retry_at, self.command_id),
                )
                tx.event(
                    "transition.retry_waiting",
                    self.command_id,
                    {"error": detail, "retry_at": retry_at},
                )
            return False
        if not isinstance(error, (ToolError, StoreError)):
            return False
        with self.store.tx(Actor.harness()) as tx:
            tx.execute(
                "UPDATE transition_commands SET result=?,completed_at=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "ok": False,
                            "kind": "transition_refused",
                            "problems": error.problems,
                            "error": str(error),
                        }
                    ),
                    tx.at,
                    self.command_id,
                ),
            )
            tx.event("transition.refused", self.command_id, {"problems": error.problems})
        return True


def command_identity(ctx: ToolContext, tool: str, operation_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"transition:{ctx.coworld().coworld_id}:{ctx.actor}:{tool}:{operation_id}",
        )
    )


def receipt(store: Store, command_id: str) -> dict:
    row = store.one("SELECT * FROM transition_commands WHERE id=?", (command_id,))
    if row is None:
        raise StoreError(["unknown transition command"])
    if row["result"] is not None:
        result = json.loads(row["result"])
        state = "applied" if result["ok"] else {"transition_refused": "refused"}[result["kind"]]
        return {**result, "command_id": command_id, "transition_state": state}
    return {
        "transition_state": "pending",
        "kind": "transition_pending",
        "command_id": command_id,
        "owner": row["owner_instance"] or "workspace coordinator",
        "reason": row["last_error"] or "awaiting coordinator application",
        "trigger": "coordinator retries after backoff"
        if row["retry_at"]
        else "coordinator processes this durable command",
        "retry_at": row["retry_at"],
        "attempts": row["attempts"],
    }


def submit(call: ToolCall) -> dict:
    return request(call.ctx, call.spec.name, call.args, call.args["operation_id"])


def request(ctx: ToolContext, tool: str, args: dict, operation_id: str) -> dict:
    command_id = command_identity(ctx, tool, operation_id)
    payload = json.dumps(args, sort_keys=True)
    # Acceptance and execution retirement share the admission gate. A persisted report
    # retains its acceptance authority if its worker exits before application.
    with admissions.dispatch_lock(ctx.wstore), ctx.store.tx(ctx.actor) as tx:
        prior = tx.one("SELECT * FROM transition_commands WHERE id=?", (command_id,))
        if prior is not None:
            if (
                prior["tool"],
                prior["args"],
                prior["role"],
                prior["actor"],
                prior["actor_epoch"],
            ) != (
                tool,
                payload,
                ctx.role,
                str(ctx.actor),
                ctx.actor.epoch,
            ):
                raise StoreError(["operation_id already belongs to a different transition intent"])
        else:
            issuer_attempt_id = None
            if (
                ctx.role == "researcher"
                and tool == "task"
                and args["action"] in {"complete", "wait", "ask", "release", "propose_submission"}
            ):
                from research_core.store import tasks
                from research_core.tools import _args as arguments

                task_id = arguments.record_number(args["task"], "task", "task")
                tasks.require_holder(ctx.store, task_id, ctx.actor)
                assignment = ctx.wstore.one(
                    "SELECT a.current_attempt_id,a.state,execution.status FROM task_assignments a"
                    " JOIN client_admissions execution ON execution.op_id=a.current_attempt_id"
                    " WHERE a.session_id=? AND a.task_id=?",
                    (ctx.actor.id, task_id),
                )
                if (
                    assignment is None
                    or assignment["state"] != "held"
                    or assignment["status"] not in {"queued", "running"}
                ):
                    raise ToolError("This worker execution no longer has task transition authority")
                issuer_attempt_id = assignment["current_attempt_id"]
            tx.execute(
                "INSERT INTO transition_commands(id,tool,args,role,actor,actor_epoch,"
                "issuer_attempt_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    tool,
                    payload,
                    ctx.role,
                    str(ctx.actor),
                    ctx.actor.epoch,
                    issuer_attempt_id,
                    tx.at,
                ),
            )
            tx.event("transition.requested", command_id, {"tool": tool})
    deadline = time.monotonic() + REQUEST_WAIT_SECONDS
    wait = threading.Event()
    while time.monotonic() < deadline:
        result = receipt(ctx.store, command_id)
        if CommandReceipt.parse(result).state != "pending":
            return result
        wait.wait(0.1)
    return receipt(ctx.store, command_id)


def apply(ctx, instance_id: str) -> None:
    """Only the lifetime coordinator calls this. Recovery reuses the exact command identity."""
    if ctx.store is None:
        return
    from research_core.tools import task

    store = ctx.store
    for command in store.q("SELECT * FROM transition_commands WHERE result IS NULL ORDER BY rowid"):
        if command["retry_at"] is not None and parse_iso(store.now()) < parse_iso(
            command["retry_at"]
        ):
            continue
        with store.tx(Actor.harness()) as tx:
            tx.execute(
                "UPDATE transition_commands SET attempts=attempts+1,owner_instance=?,"
                "last_error=NULL,retry_at=NULL WHERE id=?",
                (instance_id, command["id"]),
            )
        actor = replace(Actor.parse(command["actor"]), epoch=command["actor_epoch"])
        tool_ctx = ToolContext(
            role=command["role"],
            actor=actor,
            workspace=ctx.ws,
            env={
                **ctx.env,
                "IDE_SESSION_ID": actor.id if actor.is_session else "",
                "IDE_ACTOR": str(actor),
                "IDE_TRANSITION_ID": command["id"],
                "IDE_TRANSITION_ATTEMPT_ID": command["issuer_attempt_id"] or "",
            },
            coordinator_instance=instance_id,
        )
        tool_ctx.wstore = ctx.wstore
        tool_ctx.store = store
        session = ctx.wstore.one("SELECT profile FROM sessions WHERE id=?", (actor.id,))
        if session is not None:
            tool_ctx.env = {**tool_ctx.env, "HERMES_PROFILE_NAME": session["profile"]}
        args = json.loads(command["args"])
        with CommandApplication(store, command["id"]):
            # Lock order is admission lock, then Coworld writer, everywhere.
            with admissions.dispatch_lock(ctx.wstore), store.atomic(actor) as tx:
                effect = ctx.wstore.one(
                    "SELECT result FROM transition_effects WHERE command_id=?", (command["id"],)
                )
                if effect is not None:
                    result = json.loads(effect["result"])
                elif command["tool"] == "task":
                    call = ToolCall(spec=task.SPEC, args=args, ctx=tool_ctx, action=args["action"])
                    result = task.execute(call)
                elif command["tool"] == "workspace":
                    from research_core.tools import workspace

                    result = workspace.handle(
                        ToolCall(
                            spec=workspace.SPEC, args=args, ctx=tool_ctx, action=args["action"]
                        )
                    )
                elif command["tool"] == "capability":
                    from research_core.store import capabilities

                    result = capabilities.report_failure(store, actor, **args)
                elif command["tool"] == "store":
                    from research_core.store import cli

                    if args["op"] not in cli.ALLOWED or actor.is_session:
                        raise StoreError(
                            ["store commands are restricted to user and harness edits"]
                        )
                    stores = cli.Stores(ctx.ws, ctx.wstore, ctx.coworld.coworld_id)
                    stores._store = store
                    result = {
                        "record": cli._call(
                            args["op"],
                            "coworld",
                            stores,
                            actor,
                            args["args"],
                            coordinator_instance=instance_id,
                        )
                    }
                else:
                    raise StoreError(["unknown durable transition tool"])
                result.setdefault("ok", True)
                tx.execute(
                    "UPDATE transition_commands SET result=?,completed_at=? WHERE id=?",
                    (json.dumps(result, default=str), tx.at, command["id"]),
                )
                tx.event("transition.applied", command["id"], {"owner": instance_id})


def record_effect(tx, env, result: dict) -> None:
    """Commit a cross-store command's authoritative workspace effect and recovery receipt."""
    if "IDE_TRANSITION_ID" in env:
        tx.execute(
            "INSERT INTO transition_effects(command_id,result,committed_at) VALUES (?,?,?)",
            (env["IDE_TRANSITION_ID"], json.dumps(result), tx.at),
        )


def durable_tool(spec: ToolSpec, *, mutations: set[str] | None = None) -> ToolSpec:
    """Expose caller-stable operation identity on every command-producing tool action."""
    actions = tuple(
        replace(
            action,
            params={
                **action.params,
                "operation_id": {
                    "type": "string",
                    "description": "Unique intent ID. Reuse after a lost or pending reply; "
                    "inspect progress with task.command and the returned command_id.",
                },
            },
            required=(*action.required, "operation_id"),
        )
        if (
            action.name in mutations
            if mutations is not None
            else action.name not in {"read", "list", "workers", "command"}
        )
        else action
        for action in spec.actions
    )
    return tool(replace(spec, actions=actions))


def report_capability(ctx: ToolContext, name: str, **details) -> dict:
    """A tool reports a failure; the coordinator creates or joins its one repair task."""
    if ctx.coordinator_instance is not None:
        from research_core.store import capabilities

        return capabilities.report_failure(ctx.store, ctx.actor, name, **details)
    repaired = ctx.store.one(
        "SELECT COALESCE(MAX(seq),0) FROM events WHERE"
        " (kind='capability.repaired' AND subject=?) OR"
        " (kind IN ('task.completed','task.failed','task.abandoned','task.rejected')"
        " AND subject IN (SELECT 'T-' || id FROM tasks WHERE capability=?))",
        (name, name),
    )[0]
    operation_id = f"failure:{name}:{repaired}"
    prior = ctx.store.one(
        "SELECT args FROM transition_commands WHERE id=?",
        (command_identity(ctx, "capability", operation_id),),
    )
    intent = json.loads(prior["args"]) if prior is not None else {"name": name, **details}
    result = request(ctx, "capability", intent, operation_id)
    state = CommandReceipt.parse(result).state
    if state == "pending":
        return {"name": name, "maintenance_task": None, "transition": result}
    if state == "refused":
        raise StoreError(result["problems"])
    return result

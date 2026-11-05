"""Translate Hermes lifecycle IDs into OpenTelemetry spans; the SDK owns trace mechanics.

Native callbacks can arrive on different workers, so parent contexts are passed explicitly.
No prompts, arguments, results or exception messages are recorded. Observed callback bounds
avoid Hermes's cumulative retry clock. Missing terminal hooks never imply completion.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, StatusCode

from research_core import telemetry
from research_core.tools.catalog import Role


@dataclass
class Turn:
    span: Span
    session_id: str
    complete: bool = True
    linked: bool = False
    attempts: dict[str, Span] = field(default_factory=dict)
    tools: dict[str, Span] = field(default_factory=dict)


def active_native_turns(current_turn_id: str) -> set[str]:
    """Read authoritative leases through the pinned runtime's routed database contract."""
    from hermes_state import SessionDB

    native = SessionDB(read_only=True)
    try:
        with closing(sqlite3.connect(f"file:{native.db_path}?mode=ro", uri=True)) as connection:
            holders = connection.execute(
                "SELECT holder FROM session_turn_leases WHERE expires_at>?", (time.time(),)
            ).fetchall()
        active = {
            holder.partition(":turn=")[2].rpartition(":platform=")[0] for (holder,) in holders
        }
        if current_turn_id not in active:
            raise RuntimeError("Tracing callback does not hold the pinned native turn lease")
        return active
    finally:
        native.close()


def admission_trace(
    session_id: str, conversation_history: list | None
) -> tuple[Context | None, dict[str, str]]:
    """Recover the admitted input's W3C context after queueing, restarts, or a worker switch."""
    from research_core import layout
    from research_core.plugin.executor import profile_env
    from research_core.store.db import Store

    if not conversation_history:
        return None, {}
    message = next(m for m in reversed(conversation_history) if m["role"] == "user")
    workspace = layout.from_env(profile_env())
    with Store.open(workspace.store_path, kind="workspace") as store:
        row = store.one(
            "SELECT op_id,run_id,payload FROM client_admissions"
            " WHERE session_id=? AND kind='message' AND native_message_id=?",
            (session_id, message["_row_id"]),
        )
        if row is None:
            # A native continuation is a new transcript row for the same admitted input.
            resumed = store.q(
                "SELECT op_id,run_id,payload FROM client_admissions"
                " WHERE session_id=? AND kind='message' AND resumes>0 AND dispatch_started=1"
                " AND status IN ('queued','running')",
                (session_id,),
            )
            if len(resumed) > 1:
                raise RuntimeError("More than one resumed input owns this session")
            row = resumed[0] if resumed else None
    if row is None:
        return None, {}
    payload = json.loads(row["payload"])
    return telemetry.propagator.extract(payload.get("trace_context", {})), {
        "op.id": row["op_id"],
        **({"run.id": row["run_id"]} if row["run_id"] is not None else {}),
    }


def make_instrumentation(
    role: Role,
) -> tuple[dict[str, Callable[..., None]], Callable[[str], Span]]:
    turns: dict[str, Turn] = {}
    lock = threading.Lock()

    def incomplete(turn: Turn, reason: str) -> None:
        for current in (*turn.attempts.values(), *turn.tools.values(), turn.span):
            current.set_attributes({"observation.complete": False, "observation.reason": reason})
            current.set_status(StatusCode.ERROR)
            current.end()  # Observation end, explicitly not native completion.

    def discard_session(*, session_id: str, reason: str, **_: Any) -> None:
        with lock:
            for turn_id, turn in list(turns.items()):
                if turn.session_id == session_id:
                    incomplete(turn, reason)
                    del turns[turn_id]

    def pre_llm_call(
        *,
        session_id: str,
        task_id: str,
        turn_id: str,
        conversation_history: list | None = None,
        **_: Any,
    ) -> None:
        with lock:
            active = active_native_turns(turn_id)
            for previous, turn in list(turns.items()):
                if previous not in active:
                    incomplete(turn, "native_lease_released")
                    del turns[previous]
            if len(turns) >= 256:
                with telemetry.span("hermes.observation", reason="tracking_capacity", role=role):
                    return
            current = telemetry.tracer().start_span(
                "invoke_agent",
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": role,
                    "session.id": session_id,
                    "task.id": task_id,
                    "turn.id": turn_id,
                    "observation.boundary": "pre_llm_call_to_session_end",
                },
            )
            turns[turn_id] = Turn(current, session_id)

    def pre_api_request(
        *,
        turn_id: str,
        api_request_id: str,
        retry_count: int,
        conversation_history: list | None = None,
        **_: Any,
    ) -> None:
        with lock:
            if turn_id not in turns:
                return
            turn = turns[turn_id]
            if not turn.linked:
                parent, admission = admission_trace(turn.session_id, conversation_history)
                turn.span.set_attributes(admission)
                if parent is not None:
                    origin = trace.get_current_span(parent).get_span_context()
                    if origin.is_valid:
                        turn.span.add_link(origin)
                turn.linked = True
            turn.attempts[api_request_id] = telemetry.tracer().start_span(
                "chat",
                kind=SpanKind.CLIENT,
                context=trace.set_span_in_context(turn.span),
                attributes={
                    "gen_ai.operation.name": "chat",
                    "request.id": api_request_id,
                    "retry.count": retry_count,
                },
            )

    def model_done(
        *,
        turn_id: str,
        api_request_id: str,
        model: str,
        provider: str,
        failed: bool = False,
        usage: dict | None = None,
        status_code: int | None = None,
        first_chunk_at: float | None = None,
        **_: Any,
    ) -> None:
        with lock:
            if turn_id not in turns:
                return
            turn = turns[turn_id]
            current = turn.attempts.pop(api_request_id, None)
            if current is None:
                turn.complete = False
                turn.span.add_event("observation.missing_start", {"kind": "model"})
                return
            current.set_attributes(
                {"gen_ai.request.model": model, "gen_ai.provider.name": provider}
            )
            if status_code is not None:
                current.set_attribute("http.response.status_code", status_code)
            if usage is not None:
                for source, target in (
                    ("prompt_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("cache_read_tokens", "cache_read.input_tokens"),
                    ("cache_write_tokens", "cache_write.input_tokens"),
                ):
                    if usage[source] > 0:
                        current.set_attribute(f"gen_ai.usage.{target}", usage[source])
            if first_chunk_at is not None:
                current.add_event("response.first_chunk", timestamp=int(first_chunk_at * 1e9))
            current.set_attribute("outcome", "failed" if failed else "succeeded")
            if failed:
                current.set_status(StatusCode.ERROR)
            current.end()

    def pre_tool_call(*, turn_id: str, tool_call_id: str, **_: Any) -> None:
        with lock:
            if turn_id in turns:
                turn = turns[turn_id]
                turn.tools[tool_call_id] = telemetry.tracer().start_span(
                    "execute_tool",
                    context=trace.set_span_in_context(turn.span),
                    attributes={
                        "gen_ai.operation.name": "execute_tool",
                        "gen_ai.tool.call.id": tool_call_id,
                        "tool.boundary": "native",
                    },
                )

    def post_tool_call(
        *,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        **_: Any,
    ) -> None:
        with lock:
            if turn_id not in turns:
                return
            turn = turns[turn_id]
            current = turn.tools.pop(tool_call_id, None)
            if current is None:
                turn.complete = False
                turn.span.add_event("observation.missing_start", {"kind": "tool"})
                return
            current.set_attributes({"gen_ai.tool.name": tool_name, "outcome": status})
            if status != "ok":
                current.set_status(StatusCode.ERROR)
            current.end()

    def on_session_end(
        *,
        turn_id: str,
        completed: bool,
        failed: bool,
        interrupted: bool,
        **_: Any,
    ) -> None:
        with lock:
            if turn_id not in turns:
                return
            turn = turns.pop(turn_id)
            if turn.attempts or turn.tools or not turn.complete:
                incomplete(turn, "missing_callbacks")
                return
            turn.span.set_attribute("observation.complete", True)
            turn.span.set_attribute(
                "outcome",
                "interrupted"
                if interrupted
                else "succeeded"
                if completed and not failed
                else "failed",
            )
            if failed or interrupted or not completed:
                turn.span.set_status(StatusCode.ERROR)
            turn.span.end()

    def session_span(session_id: str) -> Span:
        # Hermes invokes registered tool handlers on isolated workers. The coordinator permits
        # exactly one active turn per native session; bind that turn explicitly on the worker.
        with lock:
            return next(
                (turn.span for turn in turns.values() if turn.session_id == session_id),
                trace.INVALID_SPAN,
            )

    hooks = {
        "pre_llm_call": pre_llm_call,
        "pre_api_request": pre_api_request,
        "post_api_request": model_done,
        "api_request_error": partial(model_done, failed=True),
        "pre_tool_call": pre_tool_call,
        "post_tool_call": post_tool_call,
        "on_session_end": on_session_end,
        **{
            name: partial(discard_session, reason=name)
            for name in ("on_session_finalize", "on_session_reset", "on_session_delete")
        },
    }
    return hooks, session_span

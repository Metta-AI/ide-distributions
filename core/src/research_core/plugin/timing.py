"""Payload-free timings from the pinned Hermes lifecycle observer contract.

The turn boundary is pre_llm_call -> on_session_end, after session/prompt setup.
Model intervals use the observed pre-request timestamp and native response end;
tool windows use observed pre/post callback timestamps. Unpaired tools have unknown bounds.
Union accounting
clips intervals to the observed turn; nested/concurrent work is never summed.
Stdlib only: this module executes in Hermes's interpreter.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import partial
from typing import Any, Literal

from research_core.tools.catalog import Role


@dataclass(frozen=True)
class TimingEvent:
    event: str
    schema_version: Literal[1] = field(default=1, init=False)
    stage: Literal["terminal"] = field(default="terminal", init=False)
    role: Role
    workspace_id: str | None
    runtime_id: str | None
    session_id: str
    task_id: str
    turn_id: str
    op_id: str | None
    run_id: str | None
    sequence: int
    started_at: float | None
    ended_at: float
    start_offset_ms: float | None
    elapsed_ms: float | None

    def emit(self) -> None:
        sys.stderr.write(json.dumps(asdict(self), separators=(",", ":")) + "\n")
        sys.stderr.flush()


@dataclass(frozen=True)
class ModelAttempt(TimingEvent):
    api_request_id: str
    call_index: int
    attempt_index: int
    native_retry_count: int | None
    native_started_at: float
    native_elapsed_ms: float
    model: str
    provider: str
    api_mode: str
    response_model: str | None
    upstream_provider: str | None
    outcome: Literal["succeeded", "failed"]
    first_chunk_ms: float | None
    post_first_chunk_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    prompt_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cache_reporting: Literal["positive_normalized", "unavailable_or_zero"]
    retryable: bool | None
    status_code: int | None
    inter_attempt_gap_ms: float | None
    retry_backoff_ms: None = None
    timestamp_source: str = "observed_pre_api_hook_to_native_response_end"


@dataclass(frozen=True)
class NativeTool(TimingEvent):
    api_request_id: str
    tool_call_id: str
    tool: str
    status: str
    outcome: Literal["succeeded", "failed"]
    repeated_tool_index: int
    execution_ms: int
    timestamp_source: str = "native_pre_post_tool_hooks"


@dataclass(frozen=True)
class TurnTiming(TimingEvent):
    boundary: str
    outcome: Literal["succeeded", "failed", "interrupted"]
    model_count: int
    tool_count: int
    model_busy_ms: float
    tool_busy_ms: float
    accounted_busy_ms: float
    overlap_ms: float
    unaccounted_ms: float
    accounting_complete: bool
    missing_model_attempts: int
    missing_tool_callbacks: int
    is_first_turn: bool
    workspace_total_ms: float | None
    admission_to_dispatch_ms: float | None
    dispatch_to_hook_ms: float | None
    admission_created_at: float | None
    admitted_at: float | None
    session_restore_ms: None = None
    provider_cache_miss: None = None
    retry_backoff_ms: None = None


@dataclass(frozen=True)
class AttemptStart:
    index: int
    native_retry_count: int
    started_at: float
    inter_attempt_gap_ms: float | None


@dataclass
class Turn:
    session_id: str
    task_id: str
    started_at: float
    is_first_turn: bool
    admission: Admission | None = None
    sequence: int = 0
    attempts: dict[str, AttemptStart] = field(default_factory=dict)
    attempt_counts: dict[str, int] = field(default_factory=dict)
    completed_model_attempts: int = 0
    models: list[tuple[float, float]] = field(default_factory=list)
    tools: list[tuple[float, float]] = field(default_factory=list)
    tool_counts: dict[str, int] = field(default_factory=dict)
    tool_ids: set[str] = field(default_factory=set)
    tool_starts: dict[str, float] = field(default_factory=dict)
    unpaired_tool_callbacks: int = 0
    last_attempt_end: dict[str, float] = field(default_factory=dict)
    unpaired_model_callbacks: int = 0

    def coordinates(self, role: Role, turn_id: str, start: float, end: float) -> dict:
        self.sequence += 1
        return dict(
            role=role,
            workspace_id=os.environ.get("IDE_WORKSPACE_ID"),
            runtime_id=os.environ.get("IDE_RUNTIME_ID"),
            session_id=self.session_id,
            task_id=self.task_id,
            turn_id=turn_id,
            op_id=None if self.admission is None else self.admission.op_id,
            run_id=None if self.admission is None else self.admission.run_id,
            sequence=self.sequence,
            started_at=start,
            ended_at=end,
            start_offset_ms=round((start - self.started_at) * 1000, 3),
            elapsed_ms=round((end - start) * 1000, 3),
        )


@dataclass(frozen=True)
class Admission:
    op_id: str
    run_id: str | None
    created_at: float
    admitted_at: float | None


def read_admission(
    *, session_id: str, native_message_id: str | int | None = None, op_id: str | None = None
) -> Admission | None:
    """Read the existing exact native-message binding; never guess the latest run."""
    from research_core import layout
    from research_core.plugin.executor import profile_env
    from research_core.store.db import Store

    workspace = layout.from_env(profile_env())
    store = Store.open(workspace.store_path, kind="workspace")
    try:
        row = store.one(
            "SELECT op_id,run_id,created_at,admitted_at FROM client_admissions"
            " WHERE session_id=? AND kind='message' AND "
            + ("op_id=?" if op_id is not None else "native_message_id=?"),
            (session_id, op_id if op_id is not None else native_message_id),
        )
        if row is None:
            return None  # Background turns have no client message admission.
        return Admission(
            row["op_id"],
            row["run_id"],
            datetime.fromisoformat(row["created_at"]).timestamp(),
            None
            if row["admitted_at"] is None
            else datetime.fromisoformat(row["admitted_at"]).timestamp(),
        )
    finally:
        store.close()


def union_seconds(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    """Length of the clipped interval union, including concurrent/nested intervals once."""
    total, right = 0.0, start
    for left, stop in sorted(intervals):
        left, stop = max(start, left, right), min(end, stop)
        if stop > left:
            total += stop - left
            right = stop
    return total


def active_native_turns(current_turn_id: str) -> set[str]:
    """Read the pinned runtime's authoritative conversation leases without mutating it.

    The public SessionDB constructor resolves multiplexed profile paths. The lease table
    and holder marker are pinned runtime contracts, verified by installed-agent tests.
    Verify this callback's own lease before using the snapshot to reclaim any state.
    """
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
            raise RuntimeError("Timing callback does not hold the pinned native turn lease")
        return active
    finally:
        native.close()


# Bounded diagnostics only. Capacity rejects NEW tracking, never evicts another live turn.
MAX_TRACKED_TURNS = 256


@dataclass(frozen=True)
class MissingTiming:
    event: str = field(default="workspace_timing_state", init=False)
    schema_version: int = field(default=1, init=False)
    stage: str = field(default="diagnostic", init=False)
    accounting_complete: bool = field(default=False, init=False)
    role: Role
    workspace_id: str | None
    runtime_id: str | None
    session_id: str
    turn_id: str
    reason: str
    callback: str
    retained_turns: int

    def emit(self) -> None:
        sys.stderr.write(json.dumps(asdict(self), separators=(",", ":")) + "\n")
        sys.stderr.flush()


def make_hooks(role: Role) -> dict[str, Callable[..., None]]:
    turns: dict[str, Turn] = {}
    lock = threading.Lock()  # Hermes may dispatch bounded callbacks on different workers.

    def missing(turn_id: str, session_id: str, reason: str, callback: str) -> MissingTiming:
        event = MissingTiming(
            role,
            os.environ.get("IDE_WORKSPACE_ID"),
            os.environ.get("IDE_RUNTIME_ID"),
            session_id,
            turn_id,
            reason,
            callback,
            len(turns),
        )
        event.emit()
        return event

    def retained(turn_id: str, callback: str) -> Turn | MissingTiming:
        if turn_id in turns:
            return turns[turn_id]
        return missing(turn_id, "", "untracked_callback", callback)

    def discard_session(*, session_id: str, reason: str, **_: Any) -> None:
        with lock:
            for turn_id, turn in list(turns.items()):
                if turn.session_id == session_id:
                    del turns[turn_id]
                    missing(turn_id, session_id, reason, "session_boundary")

    def pre_llm_call(
        *, session_id: str, task_id: str, turn_id: str, is_first_turn: bool, **_: Any
    ) -> None:
        observed_start = time.time()
        with lock:
            # A leased turn can be between calls or in backoff. Only authoritative
            # lease release/expiry proves another session's orphan is no longer live.
            active = active_native_turns(turn_id)
            for previous, turn in list(turns.items()):
                if previous not in active:
                    del turns[previous]
                    missing(previous, turn.session_id, "native_lease_released", "pre_llm_call")
            if len(turns) >= MAX_TRACKED_TURNS:
                missing(turn_id, session_id, "tracking_capacity", "pre_llm_call")
                return
            turns[turn_id] = Turn(session_id, task_id, observed_start, is_first_turn)

    def pre_api_request(
        *,
        turn_id: str,
        api_request_id: str,
        retry_count: int,
        started_at: float,
        conversation_history: list | None = None,
        **_: Any,
    ) -> None:
        observed_start = time.time()
        with lock:
            turn = retained(turn_id, "pre_api_request")
            if isinstance(turn, MissingTiming):
                return
            if turn.admission is None and conversation_history:
                current = next(m for m in reversed(conversation_history) if m["role"] == "user")
                turn.admission = read_admission(
                    session_id=turn.session_id, native_message_id=current["_row_id"]
                )
            previous = turn.last_attempt_end.get(api_request_id)
            index = turn.attempt_counts[api_request_id] = (
                turn.attempt_counts.get(api_request_id, 0) + 1
            )
            turn.attempts[api_request_id] = AttemptStart(
                index,
                retry_count,
                observed_start,
                None if previous is None else (observed_start - previous) * 1000,
            )

    def model_done(
        *,
        turn_id: str,
        api_request_id: str,
        api_call_count: int,
        started_at: float,
        ended_at: float,
        model: str,
        provider: str,
        api_mode: str,
        first_chunk_at: float | None = None,
        usage: dict | None = None,
        response_model: str | None = None,
        response: dict | None = None,
        failed: bool = False,
        retryable: bool | None = None,
        status_code: int | None = None,
        **_: Any,
    ) -> None:
        with lock:
            turn = retained(turn_id, "model_done")
            if isinstance(turn, MissingTiming):
                return
            attempt = turn.attempts.pop(api_request_id, None)
            if attempt is None:
                turn.unpaired_model_callbacks += 1
            turn.completed_model_attempts += 1
            if attempt is not None:
                turn.models.append((attempt.started_at, ended_at))
            turn.last_attempt_end[api_request_id] = ended_at
            # Canonical usage defaults missing cache fields to zero. Positive buckets are
            # evidence of reported usage; zero cannot distinguish absence from an actual zero.
            read = usage["cache_read_tokens"] if usage else 0
            write = usage["cache_write_tokens"] if usage else 0
            first = (
                None
                if first_chunk_at is None or attempt is None
                else (first_chunk_at - attempt.started_at) * 1000
            )
            ModelAttempt(
                event="workspace_model_attempt",
                **{
                    **turn.coordinates(
                        role, turn_id, ended_at if attempt is None else attempt.started_at, ended_at
                    ),
                    **(
                        {"started_at": None, "start_offset_ms": None, "elapsed_ms": None}
                        if attempt is None
                        else {}
                    ),
                },
                api_request_id=api_request_id,
                call_index=api_call_count,
                attempt_index=attempt.index if attempt else 0,
                native_retry_count=attempt.native_retry_count if attempt else None,
                native_started_at=started_at,
                native_elapsed_ms=round((ended_at - started_at) * 1000, 3),
                model=model,
                provider=provider,
                api_mode=api_mode,
                response_model=response_model,
                upstream_provider=None if response is None else response.get("upstream_provider"),
                outcome="failed" if failed else "succeeded",
                first_chunk_ms=first,
                post_first_chunk_ms=None
                if first_chunk_at is None
                else (ended_at - first_chunk_at) * 1000,
                input_tokens=usage["input_tokens"] if usage else None,
                output_tokens=usage["output_tokens"] if usage else None,
                prompt_tokens=usage["prompt_tokens"] if usage else None,
                cache_read_tokens=read if read > 0 else None,
                cache_write_tokens=write if write > 0 else None,
                cache_reporting="positive_normalized"
                if read > 0 or write > 0
                else "unavailable_or_zero",
                retryable=retryable,
                status_code=status_code,
                inter_attempt_gap_ms=attempt.inter_attempt_gap_ms if attempt else None,
            ).emit()

    def api_request_error(**kwargs: Any) -> None:
        model_done(**kwargs, failed=True)

    def pre_tool_call(*, turn_id: str, tool_call_id: str, **_: Any) -> None:
        observed_start = time.time()
        with lock:
            turn = retained(turn_id, "pre_tool_call")
            if isinstance(turn, MissingTiming):
                return
            turn.tool_starts[tool_call_id] = observed_start

    def post_tool_call(
        *,
        turn_id: str,
        tool_call_id: str,
        api_request_id: str,
        tool_name: str,
        duration_ms: int,
        status: str,
        **_: Any,
    ) -> None:
        observed_end = time.time()
        with lock:
            turn = retained(turn_id, "post_tool_call")
            if isinstance(turn, MissingTiming):
                return
            # Native executor owns one terminal callback per ID. Keep retries distinct;
            # suppress only a repeated delivery of that same completed native tool call.
            if tool_call_id in turn.tool_ids:
                return
            turn.tool_ids.add(tool_call_id)
            end = observed_end
            start = turn.tool_starts.pop(tool_call_id, None)
            if start is None:
                turn.unpaired_tool_callbacks += 1
            else:
                turn.tools.append((start, end))
            count = turn.tool_counts[tool_name] = turn.tool_counts.get(tool_name, 0) + 1
            NativeTool(
                event="workspace_native_tool",
                **{
                    **turn.coordinates(role, turn_id, end if start is None else start, end),
                    **(
                        {"started_at": None, "start_offset_ms": None, "elapsed_ms": None}
                        if start is None
                        else {}
                    ),
                },
                api_request_id=api_request_id,
                tool_call_id=tool_call_id,
                tool=tool_name,
                status=status,
                outcome="succeeded" if status == "ok" else "failed",
                repeated_tool_index=count,
                execution_ms=duration_ms,
            ).emit()

    def on_session_end(
        *, turn_id: str, completed: bool, failed: bool, interrupted: bool, **_: Any
    ) -> None:
        observed_end = time.time()
        with lock:
            turn = retained(turn_id, "on_session_end")
            if isinstance(turn, MissingTiming):
                return
            del turns[turn_id]
            end = observed_end
            if turn.admission is not None:
                turn.admission = read_admission(
                    session_id=turn.session_id, op_id=turn.admission.op_id
                )
            admission = turn.admission
            admitted = None if admission is None else admission.admitted_at
            model_busy = union_seconds(turn.models, turn.started_at, end)
            tool_busy = union_seconds(turn.tools, turn.started_at, end)
            accounted = union_seconds(turn.models + turn.tools, turn.started_at, end)
            missing = len(turn.attempts) + turn.unpaired_model_callbacks
            missing_tools = len(turn.tool_starts) + turn.unpaired_tool_callbacks
            TurnTiming(
                event="workspace_turn",
                **turn.coordinates(role, turn_id, turn.started_at, end),
                boundary="pre_llm_hook_to_session_end",
                outcome="interrupted"
                if interrupted
                else "succeeded"
                if completed and not failed
                else "failed",
                model_count=turn.completed_model_attempts,
                tool_count=len(turn.tool_ids),
                model_busy_ms=round(model_busy * 1000, 3),
                tool_busy_ms=round(tool_busy * 1000, 3),
                accounted_busy_ms=round(accounted * 1000, 3),
                overlap_ms=round((model_busy + tool_busy - accounted) * 1000, 3),
                unaccounted_ms=round((end - turn.started_at - accounted) * 1000, 3),
                accounting_complete=missing == 0 and missing_tools == 0,
                missing_model_attempts=missing,
                missing_tool_callbacks=missing_tools,
                is_first_turn=turn.is_first_turn,
                workspace_total_ms=None
                if admission is None
                else round((end - admission.created_at) * 1000, 3),
                admission_to_dispatch_ms=None
                if admitted is None
                else round((admitted - admission.created_at) * 1000, 3),
                dispatch_to_hook_ms=None
                if admitted is None
                else round((turn.started_at - admitted) * 1000, 3),
                admission_created_at=None if admission is None else admission.created_at,
                admitted_at=admitted,
            ).emit()

    return {
        "pre_llm_call": pre_llm_call,
        "pre_api_request": pre_api_request,
        "post_api_request": model_done,
        "api_request_error": api_request_error,
        "pre_tool_call": pre_tool_call,
        "post_tool_call": post_tool_call,
        "on_session_end": on_session_end,
        "on_session_finalize": partial(discard_session, reason="session_finalize"),
        "on_session_reset": partial(discard_session, reason="session_reset"),
        "on_session_delete": partial(discard_session, reason="session_delete"),
    }

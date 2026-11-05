import json
from unittest.mock import patch

import pytest

from research_core.plugin import timing


def start(hooks, monkeypatch, now=100):
    monkeypatch.setattr(timing.time, "time", lambda: now)
    hooks["pre_llm_call"](
        session_id="s1",
        task_id="s1",
        turn_id="turn-1",
        is_first_turn=False,
        user_message="secret-prompt",
    )


def model(hooks, start, end, request="api-1", retry=0, failed=False, native_start=None, **extra):
    with patch.object(timing.time, "time", return_value=start):
        hooks["pre_api_request"](
            turn_id="turn-1",
            api_request_id=request,
            retry_count=retry,
            started_at=start if native_start is None else native_start,
        )
    hooks["api_request_error" if failed else "post_api_request"](
        turn_id="turn-1",
        api_request_id=request,
        api_call_count=1,
        started_at=start if native_start is None else native_start,
        ended_at=end,
        model="model",
        provider="provider",
        api_mode="chat",
        **extra,
    )


def finish(hooks, monkeypatch, end=110):
    monkeypatch.setattr(timing.time, "time", lambda: end)
    hooks["on_session_end"](
        turn_id="turn-1",
        completed=True,
        failed=False,
        interrupted=False,
    )


def records(capsys):
    return [json.loads(line) for line in capsys.readouterr().err.splitlines()]


def test_intervals_reconcile_overlap_without_summing_concurrent_work(monkeypatch, capsys):
    hooks = timing.make_hooks("orchestrator")
    start(hooks, monkeypatch)
    model(hooks, 101, 105)
    monkeypatch.setattr(timing.time, "time", lambda: 105)
    hooks["pre_tool_call"](turn_id="turn-1", tool_call_id="tool-1", args={"secret": "token"})
    monkeypatch.setattr(timing.time, "time", lambda: 107)
    hooks["post_tool_call"](
        turn_id="turn-1",
        tool_call_id="tool-1",
        api_request_id="api-1",
        tool_name="task",
        duration_ms=1900,
        status="error",
        result="secret-token",
    )
    model(hooks, 106, 109, request="api-2")
    finish(hooks, monkeypatch)
    rows = records(capsys)
    summary = rows[-1]
    assert summary["elapsed_ms"] == 10000
    assert summary["model_busy_ms"] == 7000
    assert summary["tool_busy_ms"] == 2000
    assert summary["accounted_busy_ms"] == 8000
    assert summary["overlap_ms"] == 1000
    assert summary["unaccounted_ms"] == 2000
    assert summary["accounted_busy_ms"] + summary["unaccounted_ms"] == summary["elapsed_ms"]
    assert summary["accounting_complete"]
    assert summary["workspace_total_ms"] is None
    assert rows[1]["execution_ms"] == 1900
    assert rows[1]["started_at"] == 105
    assert rows[1]["elapsed_ms"] == 2000
    assert "secret" not in json.dumps(rows)
    assert [r["sequence"] for r in rows] == [1, 2, 3, 4]
    assert all(r["stage"] == "terminal" and r["schema_version"] == 1 for r in rows)


def test_retry_gap_and_first_chunk_have_literal_boundaries(monkeypatch, capsys):
    hooks = timing.make_hooks("researcher")
    start(hooks, monkeypatch)
    model(hooks, 101, 103, failed=True, retryable=True, status_code=429)
    model(hooks, 105, 108, retry=1, native_start=101, first_chunk_at=106)
    finish(hooks, monkeypatch)
    first, second, summary = records(capsys)
    assert first["outcome"] == "failed" and first["status_code"] == 429
    assert first["first_chunk_ms"] is None
    assert second["attempt_index"] == 2
    assert second["native_started_at"] == 101
    assert second["native_elapsed_ms"] == 7000
    assert second["elapsed_ms"] == 3000
    assert second["started_at"] == 105
    assert second["inter_attempt_gap_ms"] == 2000
    assert second["retry_backoff_ms"] is None
    assert second["first_chunk_ms"] == 1000
    assert second["post_first_chunk_ms"] == 2000
    assert summary["model_count"] == 2 and summary["model_busy_ms"] == 5000


@pytest.mark.parametrize("cache_read,expected", [(0, None), (123, 123)])
def test_normalized_cache_zero_is_unknown_not_a_cache_miss(
    monkeypatch, capsys, cache_read, expected
):
    hooks = timing.make_hooks("orchestrator")
    start(hooks, monkeypatch)
    model(
        hooks,
        101,
        102,
        usage={
            "input_tokens": 1000,
            "output_tokens": 12,
            "prompt_tokens": 1000 + cache_read,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": 0,
        },
    )
    (event,) = records(capsys)
    assert event["cache_read_tokens"] == expected
    assert event["cache_write_tokens"] is None
    assert event["cache_reporting"] == (
        "positive_normalized" if cache_read else "unavailable_or_zero"
    )


def test_unpaired_callbacks_do_not_invent_zero_duration_or_complete_accounting(monkeypatch, capsys):
    hooks = timing.make_hooks("orchestrator")
    start(hooks, monkeypatch)
    hooks["pre_api_request"](
        turn_id="turn-1",
        api_request_id="unfinished",
        retry_count=0,
        started_at=101,
    )
    hooks["post_tool_call"](
        turn_id="turn-1",
        tool_call_id="tool-1",
        api_request_id="api-1",
        tool_name="task",
        duration_ms=2000,
        status="cancelled",
    )
    finish(hooks, monkeypatch)
    tool, summary = records(capsys)
    assert tool["started_at"] is None and tool["elapsed_ms"] is None
    assert tool["execution_ms"] == 2000
    assert not summary["accounting_complete"]
    assert summary["missing_model_attempts"] == 1
    assert summary["missing_tool_callbacks"] == 1
    assert summary["tool_count"] == 1


def test_exact_native_message_binding_supplies_admission_envelope(monkeypatch, capsys):
    hooks = timing.make_hooks("orchestrator")
    start(hooks, monkeypatch)
    reads = []

    def read(**kw):
        reads.append(kw)
        return timing.Admission("op-1", None if "native_message_id" in kw else "run-1", 90, 95)

    monkeypatch.setattr(timing, "read_admission", read)
    monkeypatch.setattr(timing.time, "time", lambda: 101)
    hooks["pre_api_request"](
        turn_id="turn-1",
        api_request_id="api-1",
        retry_count=0,
        started_at=101,
        conversation_history=[{"role": "user", "_row_id": 41, "content": "secret-prompt"}],
    )
    hooks["post_api_request"](
        turn_id="turn-1",
        api_request_id="api-1",
        api_call_count=1,
        started_at=101,
        ended_at=105,
        model="model",
        provider="provider",
        api_mode="chat",
    )
    finish(hooks, monkeypatch)
    event, summary = records(capsys)
    assert event["run_id"] is None and event["op_id"] == "op-1"
    assert summary["run_id"] == "run-1"
    assert summary["workspace_total_ms"] == 20000
    assert summary["admission_to_dispatch_ms"] == 5000
    assert summary["dispatch_to_hook_ms"] == 5000
    assert summary["workspace_total_ms"] == (
        summary["admission_to_dispatch_ms"] + summary["dispatch_to_hook_ms"] + summary["elapsed_ms"]
    )
    assert summary["session_restore_ms"] is None
    assert reads == [
        {"session_id": "s1", "native_message_id": 41},
        {"session_id": "s1", "op_id": "op-1"},
    ]


def test_union_clips_nested_and_outside_intervals():
    assert timing.union_seconds([(0, 12), (3, 7), (10, 20), (22, 25)], 2, 24) == 20

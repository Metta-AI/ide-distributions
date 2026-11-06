"""Guard distribution privacy and Hermes callback correlation owned by this adapter."""

import pytest
from opentelemetry.trace import StatusCode

from research_core import telemetry
from research_core.plugin import tracing


def test_exception_status_never_records_sensitive_exception_text(spans):
    with pytest.raises(RuntimeError), telemetry.span("failure"):
        raise RuntimeError("secret-credential")
    (current,) = spans.get_finished_spans()
    assert current.status.status_code == StatusCode.ERROR
    assert "secret-credential" not in current.to_json()
    assert not current.events


def test_native_callbacks_keep_concurrent_turns_and_retry_attempts_separate(monkeypatch, spans):
    active = {"t1", "t2"}
    hooks, session_span = tracing.make_instrumentation("researcher")
    for turn_id in active:
        hooks["pre_llm_call"](session_id=turn_id, task_id=turn_id, turn_id=turn_id)
    bound = {turn_id: session_span(turn_id).get_span_context().span_id for turn_id in active}
    for turn_id in active:
        hooks["pre_api_request"](turn_id=turn_id, api_request_id="same", retry_count=0)
    hooks["api_request_error"](
        turn_id="t1",
        api_request_id="same",
        model="m",
        provider="p",
        status_code=429,
        error="secret-error",
    )
    hooks["pre_api_request"](turn_id="t1", api_request_id="same", retry_count=1)
    for turn_id in active:
        hooks["post_api_request"](turn_id=turn_id, api_request_id="same", model="m", provider="p")
        hooks["on_session_end"](turn_id=turn_id, completed=True, failed=False, interrupted=False)
    records = spans.get_finished_spans()
    roots = {s.context.span_id: s for s in records if s.name == "invoke_agent"}
    models = [s for s in records if s.name == "chat"]
    assert len(roots) == 2 and len(models) == 3
    assert all(s.parent.span_id in roots for s in models)
    assert {root.attributes["session.id"]: root.context.span_id for root in roots.values()} == bound
    retries = [s for s in models if roots[s.parent.span_id].attributes["turn.id"] == "t1"]
    assert [s.attributes["retry.count"] for s in retries] == [0, 1]
    assert retries[0].status.status_code == StatusCode.ERROR
    assert all("secret-error" not in s.to_json() for s in records)


def test_orphan_observation_ends_incomplete_without_reclaiming_active_tool(monkeypatch, spans):
    active = {"failed", "long-tool"}
    hooks, session_span = tracing.make_instrumentation("orchestrator")
    for turn_id in active:
        hooks["pre_llm_call"](session_id=turn_id, task_id=turn_id, turn_id=turn_id)
    hooks["pre_tool_call"](turn_id="long-tool", tool_call_id="tool")
    hooks["pre_llm_call"](session_id="failed", task_id="next", turn_id="next")
    (orphan,) = spans.get_finished_spans()
    assert orphan.attributes["observation.complete"] is False
    assert orphan.attributes["observation.reason"] == "next_turn_in_session"
    hooks["post_tool_call"](
        turn_id="long-tool", tool_call_id="tool", tool_name="terminal", status="ok"
    )
    hooks["on_session_end"](turn_id="long-tool", completed=True, failed=False, interrupted=False)
    assert spans.get_finished_spans()[-1].attributes["observation.complete"] is True

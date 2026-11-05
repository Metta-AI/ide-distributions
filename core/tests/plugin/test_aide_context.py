"""Owned middleware preserves native history and the current input's role and media."""

import json

import pytest

from research_core.aide_messages import context_message
from research_core.plugin.aide_context import inject_context
from research_core.plugin.messages import record_native_message
from research_core.store import admissions, sessions
from tests.test_serve import HARNESS, make_ctx


def test_context_is_bound_to_one_session_and_survives_tool_loops(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr("research_core.plugin.executor.profile_env", lambda: ctx.env)
    sessions.register(ctx.wstore, HARNESS, session_id="s", profile="orchestrator", kind="chat")
    prefixes = [
        {"role": "developer", "content": "Trusted static site instructions"},
        context_message({"label": "café 雪"}),
    ]
    user = {"role": "user", "content": [{"type": "text", "text": "question"}]}
    key = admissions.enqueue(
        ctx.wstore,
        HARNESS,
        key="input",
        session_id="s",
        profile="orchestrator",
        text="question",
        origin="client",
    )
    request = {"canonical_request": {"messages": [*prefixes, user]}}
    with ctx.wstore.tx(HARNESS) as tx:
        tx.execute(
            "UPDATE client_admissions SET request=?,dispatch_started=1 WHERE op_id=?",
            (json.dumps(request), key),
        )
    prior = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"}]
    native = {"messages": [*prior, user], "model": "model"}
    projected = inject_context(request=native, session_id="s")["request"]
    assert projected == {"messages": [*prior, *prefixes, user], "model": "model"}
    assert (
        inject_context(request=native, session_id="compressed", task_id="s")["request"] == projected
    )
    assert native["messages"] == [*prior, user]
    assert inject_context(request=projected, session_id="s") is None
    assert inject_context(request=native, session_id="other") is None
    tool = {"role": "tool", "tool_call_id": "call", "content": "result"}
    assert inject_context(request={"messages": [*prior, user, tool]}, session_id="s")["request"][
        "messages"
    ] == [*prior, *prefixes, user, tool]
    record_native_message(
        session_id="compressed", task_id="s", conversation_history=[{**user, "_row_id": 73}]
    )
    assert (
        ctx.wstore.one("SELECT native_message_id FROM client_admissions WHERE op_id=?", (key,))[0]
        == 73
    )
    with ctx.wstore.tx(HARNESS) as tx:
        tx.execute(
            "INSERT INTO client_admissions(op_id,kind,request,session_id,message,payload,"
            "status,created_at,dispatch_started) VALUES ('second','message',?,'s','','{}',"
            "'queued','t',1)",
            (json.dumps(request),),
        )
    with pytest.raises(AssertionError, match="one serialized"):
        inject_context(request=native, session_id="s")

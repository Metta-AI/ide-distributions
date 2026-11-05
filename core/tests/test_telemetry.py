"""Exercise SDK export and context at real process and HTTP boundaries."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
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


def test_real_otlp_export_and_child_process_context(monkeypatch):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    ExportTraceServiceRequest.FromString(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                )
            )
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    worker = threading.Thread(target=collector.serve_forever)
    worker.start()
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"http://127.0.0.1:{collector.server_port}/v1/traces"
    )
    monkeypatch.setenv("IDE_WORKSPACE_ID", "workspace-proof")
    monkeypatch.setenv("IDE_RUNTIME_ID", "boot-proof")
    script = '''
import os, subprocess, sys
from research_core import telemetry
with telemetry.span("parent"):
    result = subprocess.run([sys.executable, "-c", """
import os
from research_core import telemetry
with telemetry.span("child", context=telemetry.process_context(os.environ)):
    pass
telemetry._provider.force_flush()
"""], env=telemetry.process_env(os.environ), check=True)
telemetry._provider.force_flush()
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
    finally:
        collector.shutdown()
        collector.server_close()
        worker.join()
    assert len(received) == 2
    decoded = {}
    for path, request in received:
        assert path == "/v1/traces"
        for resource_spans in request.resource_spans:
            attrs = {a.key: a.value.string_value for a in resource_spans.resource.attributes}
            assert attrs["workspace.id"] == "workspace-proof"
            assert attrs["service.instance.id"] == "boot-proof"
            for scope in resource_spans.scope_spans:
                decoded.update({s.name: s for s in scope.spans})
    parent, child = decoded["parent"], decoded["child"]
    assert child.trace_id == parent.trace_id and child.parent_span_id == parent.span_id
    assert parent.start_time_unix_nano <= child.start_time_unix_nano
    assert child.end_time_unix_nano <= parent.end_time_unix_nano
    assert (
        len(
            [json.loads(line) for line in result.stderr.splitlines() if line.startswith('{"name":')]
        )
        == 2
    )


def test_native_callbacks_keep_concurrent_turns_and_retry_attempts_separate(monkeypatch, spans):
    active = {"t1", "t2"}
    monkeypatch.setattr(tracing, "active_native_turns", lambda current: active)
    hooks, middleware = tracing.make_instrumentation("researcher")
    for turn_id in active:
        hooks["pre_llm_call"](session_id=turn_id, task_id=turn_id, turn_id=turn_id)
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
    retries = [s for s in models if roots[s.parent.span_id].attributes["turn.id"] == "t1"]
    assert [s.attributes["retry.count"] for s in retries] == [0, 1]
    assert retries[0].status.status_code == StatusCode.ERROR
    assert retries[0].end_time <= retries[1].start_time
    assert all("secret-error" not in s.to_json() for s in records)


def test_orphan_observation_ends_incomplete_without_reclaiming_active_tool(monkeypatch, spans):
    active = {"failed", "long-tool"}
    monkeypatch.setattr(tracing, "active_native_turns", lambda current: active)
    hooks, middleware = tracing.make_instrumentation("orchestrator")
    for turn_id in active:
        hooks["pre_llm_call"](session_id=turn_id, task_id=turn_id, turn_id=turn_id)
    hooks["pre_tool_call"](turn_id="long-tool", tool_call_id="tool")
    active.remove("failed")
    active.add("next")
    hooks["pre_llm_call"](session_id="next", task_id="next", turn_id="next")
    (orphan,) = spans.get_finished_spans()
    assert orphan.attributes["observation.complete"] is False
    assert orphan.attributes["observation.reason"] == "native_lease_released"
    hooks["post_tool_call"](
        turn_id="long-tool", tool_call_id="tool", tool_name="terminal", status="ok"
    )
    hooks["on_session_end"](turn_id="long-tool", completed=True, failed=False, interrupted=False)
    assert spans.get_finished_spans()[-1].attributes["observation.complete"] is True

"""A collector outage must not hold a completed public tool command at interpreter exit."""

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


@contextmanager
def collector(*, acknowledge):
    received = threading.Event()
    release = threading.Event()
    requests = []
    if acknowledge:
        release.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(ExportTraceServiceRequest.FromString(body))
            received.set()
            release.wait()
            if acknowledge:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1], received, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


def tool_process(tmp_path, port):
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update(
        IDE_WORKSPACE=str(tmp_path),
        HERMES_HOME=str(tmp_path / "profile"),
        SOFTMAX_PLAYER_ID="telemetry-proof-player",
        OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{port}",
        NO_PROXY="127.0.0.1",
        TRACEPARENT="00-11111111111111111111111111111111-2222222222222222-01",
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "research_core.cli",
            "tool",
            "workspace",
            "--args",
            '{"action":"environment_guide"}',
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_tool_result_and_correlated_span_reach_collector(tmp_path):
    with collector(acknowledge=True) as (port, received, requests):
        process = tool_process(tmp_path, port)
        try:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0 and json.loads(stdout)["ok"] is True
            assert received.is_set()
            (request,) = requests
            (resource,) = request.resource_spans
            (scope,) = resource.scope_spans
            (span,) = scope.spans
            assert span.name == "execute_tool"
            assert span.trace_id.hex() == "11111111111111111111111111111111"
            assert span.parent_span_id.hex() == "2222222222222222"
            attributes = {item.key: item.value.string_value for item in span.attributes}
            assert attributes["gen_ai.tool.name"] == "workspace"
            assert attributes["tool.action"] == "environment_guide"
            assert "Failed to export" not in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def test_collector_without_response_does_not_hold_tool_result(tmp_path):
    with collector(acknowledge=False) as (port, received, requests):
        process = tool_process(tmp_path, port)
        try:
            assert received.wait(20), "tool never reached the collector"
            # The collector remains blocked until fixture cleanup. The command must return
            # its usable result independently of an export acknowledgement.
            stdout, stderr = process.communicate(timeout=3)
            assert process.returncode == 0 and json.loads(stdout)["ok"] is True
            assert requests
            assert "Failed to export" in stderr
            assert any(
                json.loads(line)["name"] == "execute_tool"
                for line in stderr.splitlines()
                if line.startswith('{"name":')
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

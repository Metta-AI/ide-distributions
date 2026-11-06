"""Telemetry outages must not block public workspace admissions or tool command results."""

import json
import os
import select
import signal
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


@contextmanager
def collector(*, response):
    received = threading.Event()
    release = threading.Event()
    changed = threading.Condition()
    spans = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            request = ExportTraceServiceRequest.FromString(body)
            with changed:
                spans.extend(
                    s
                    for r in request.resource_spans
                    for scope in r.scope_spans
                    for s in scope.spans
                )
                changed.notify_all()
            received.set()
            if response is None:
                release.wait()
                return
            self.send_response(response)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1], received, changed, spans
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join()


def environment(tmp_path, port):
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    home = tmp_path / "profile"
    home.mkdir()
    # Native execution is unavailable; local durable admission must remain available.
    (home / ".env").write_text("API_SERVER_KEY=fixture\nAPI_SERVER_PORT=1\n")
    env.update(
        IDE_WORKSPACE=str(tmp_path / "workspace"),
        HERMES_HOME=str(home),
        IDE_INFERENCE_LEASE_FILE=str(tmp_path / "missing-lease"),
        SOFTMAX_PLAYER_ID="telemetry-proof-player",
        PYTHONUNBUFFERED="1",  # The normal workspace image's effective configuration.
        OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{port}",
        OTEL_EXPORTER_OTLP_TRACES_TIMEOUT="30",
        OTEL_BSP_SCHEDULE_DELAY="1",
        OTEL_BSP_MAX_QUEUE_SIZE="4",
        OTEL_BSP_MAX_EXPORT_BATCH_SIZE="1",
        NO_PROXY="127.0.0.1",
    )
    return env


@contextmanager
def unread_stderr():
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            os.write(write_fd, b"x" * 4096)
    except BlockingIOError:
        pass
    os.set_blocking(write_fd, True)
    try:
        yield write_fd
    finally:
        os.close(write_fd)
        os.close(read_fd)


@contextmanager
def workspace_server(env, stderr):
    process = subprocess.Popen(
        [sys.executable, "-m", "research_core.cli", "serve", "--port", "0"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 20)[0], "workspace never became ready"
        line = process.stdout.readline()
        assert line.startswith("research serve: "), line
        yield line.split()[2]
    finally:
        process.send_signal(signal.SIGINT)
        try:
            # Do not consume stderr: that would release the fault under test.
            process.wait(timeout=3)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def post(url, path, body):
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        assert response.status == 200
        return json.load(response)


def open_session(url):
    body = {
        "op_id": str(uuid4()),
        "client": {"id": "proof", "kind": "desktop"},
        "session_context": {"contract_version": 1, "scope": "workspace"},
    }
    first = post(url, "/session-open", body)
    assert post(url, "/session-open", body)["session_id"] == first["session_id"]
    return first["session_id"], body["op_id"]


def test_public_admission_and_correlated_trace_reach_collector(tmp_path):
    with collector(response=200) as (port, _, changed, spans):
        env = environment(tmp_path, port)
        env["OTEL_BSP_MAX_QUEUE_SIZE"] = "2048"
        with workspace_server(env, subprocess.DEVNULL) as url:
            _, op_id = open_session(url)

            def admitted():
                return [
                    s
                    for s in spans
                    if s.name == "session.open"
                    and any(
                        a.key == "op.id" and a.value.string_value == op_id for a in s.attributes
                    )
                ]

            with changed:
                assert changed.wait_for(admitted, timeout=10), "successful admission trace was lost"
            current = admitted()[0]
            assert current.trace_id.hex() == "11111111111111111111111111111111"
            assert current.parent_span_id
            assert current.end_time_unix_nano >= current.start_time_unix_nano


@pytest.mark.parametrize("fault", ["stalled", "rejected", "invalid_exporter", "invalid_batch"])
def test_telemetry_faults_do_not_hold_public_admissions_or_exit(tmp_path, fault):
    response = 403 if fault == "rejected" else None
    with collector(response=response) as (port, received, _, _), unread_stderr() as stderr:
        env = environment(tmp_path, port)
        if fault == "invalid_exporter":
            env["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] = "invalid"
        if fault == "invalid_batch":
            env["OTEL_BSP_MAX_QUEUE_SIZE"] = "invalid"
        with workspace_server(env, stderr) as url:
            session_id, _ = open_session(url)
            if fault in ("stalled", "rejected"):
                assert received.wait(10), "faulty collector was never exercised"
            # Saturate the four-span export queues while neither stderr nor a stalled
            # collector is drained. Normal logged message admission must still succeed.
            for _ in range(12):
                post(
                    url,
                    "/message",
                    {
                        "op_id": str(uuid4()),
                        "session_id": session_id,
                        "message": {"role": "user", "content": "fixture request"},
                    },
                )
            open_session(url)


@pytest.mark.parametrize("timeout", ["30", "invalid"])
def test_public_tool_never_waits_for_export_or_stderr(tmp_path, timeout):
    with collector(response=None) as (port, received, _, _), unread_stderr() as stderr:
        env = environment(tmp_path, port)
        env["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] = timeout
        process = subprocess.Popen(
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
            stderr=stderr,
            text=True,
        )
        try:
            stdout, _ = process.communicate(timeout=3)
            assert process.returncode == 0 and json.loads(stdout)["ok"] is True
            assert not received.is_set()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

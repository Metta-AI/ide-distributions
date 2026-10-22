"""Durable wake admission across a lost HTTP response and a fresh caller."""

import json
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator.gate import wake
from orchestrator.storage import open_storage


@pytest.mark.parametrize("recovering", [False, True])
@pytest.mark.parametrize("terminal", ["failed", "interrupted", "cancelled", "completed"])
def test_wake_replays_lost_admission_without_creating_another_run(context, terminal, recovering):
    pending = (
        {
            "sequence": 7,
            "identity": "original-input",
            "request": {"session_id": "original-session", "input": "original request"},
        }
        if recovering
        else None
    )
    accepted = {}
    requests = []
    statuses = {"run_1": "running"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/v1/coordinator/pending":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"pending": pending}).encode())
                return
            if self.path.startswith("/v1/runs/"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": statuses[self.path.rsplit("/", 1)[-1]]}).encode()
                )
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "features": {
                            "runs_idempotency": {
                                "supported": True,
                                "durable": True,
                                "retention_seconds": 3600,
                            }
                        }
                    }
                ).encode()
            )

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = self.headers["Idempotency-Key"]
            accepted.setdefault(key, {"run_id": f"run_{len(accepted) + 1}"})
            statuses.setdefault(accepted[key]["run_id"], "running")
            requests.append((key, body))
            if len(requests) == 1:
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(accepted[key]).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        binding = context.home / "bridge"
        binding.mkdir()
        (binding / "config.json").write_text(
            json.dumps(
                {
                    "hermes_url": f"http://127.0.0.1:{server.server_port}",
                    "service_key": "test",
                }
            )
        )
        context.env["SOFTMAX_BRIDGE_DIRECTORY"] = str(binding)
        with closing(open_storage(context.storage_path)) as conn, conn:
            conn.execute("INSERT INTO coordinator VALUES ('session', 'session-1')")
        event = {"wakeAgent": True, "context": {"events": [{"id": 1}], "pending": []}}
        if recovering:
            event = {"wakeAgent": False}
        with pytest.raises(ConnectionError):
            wake(context, event)
        assert wake(context, event) == {"wakeAgent": False, "run_id": "run_1"}
        assert wake(context, event) == {"wakeAgent": False, "run_id": "run_1"}
        assert len(accepted) == 1 and len(requests) == 2
        assert requests[0] == requests[1]
        if recovering:
            assert requests[0][1] == {
                **pending["request"],
                "coordinator_input_id": "original-input",
            }
        statuses["run_1"] = terminal
        expected = "run_2" if terminal in {"failed", "interrupted"} else "run_1"
        assert wake(context, event)["run_id"] == expected
        assert wake(context, event)["run_id"] == expected
        assert len(accepted) == (2 if expected == "run_2" else 1)
        if expected == "run_2":
            statuses["run_2"] = terminal
            assert wake(context, event)["run_id"] == "run_3"
            statuses["run_3"] = terminal
            with pytest.raises(RuntimeError, match="failed three times"):
                wake(context, event)
            assert len(accepted) == 3
    finally:
        server.shutdown()
        worker.join()
        server.server_close()

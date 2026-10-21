"""Durable wake admission across a lost HTTP response and a fresh caller."""

import json
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator.gate import wake
from orchestrator.storage import open_storage


def test_wake_replays_lost_admission_without_creating_another_run(context):
    accepted = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
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
            accepted.setdefault(key, {"run_id": "run_1"})
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
        with pytest.raises(ConnectionError):
            wake(context, event)
        assert wake(context, event) == {"wakeAgent": False, "run_id": "run_1"}
        assert wake(context, event) == {"wakeAgent": False, "run_id": "run_1"}
        assert len(accepted) == 1 and len(requests) == 2
        assert requests[0] == requests[1]
    finally:
        server.shutdown()
        worker.join()
        server.server_close()

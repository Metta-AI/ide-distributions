"""A fresh cron caller uses only the durable native inbox after lost responses."""

import json
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orchestrator.gate import wake
from orchestrator.storage import league_results_seen, open_storage, remember_submission_session


@pytest.mark.parametrize("lost_path", ["/v1/coordinator/inputs", "/v1/runs"])
def test_wake_recovers_without_a_local_receipt(context, lost_path):
    queue = {}
    launched = []
    lost = False
    completed = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/v1/coordinator/pending"
            pending = next(iter(queue.values())) if queue and not completed else None
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"pending": pending}).encode())

        def do_POST(self):
            nonlocal lost
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers.get("Idempotency-Key") is None
            if self.path == "/v1/coordinator/inputs":
                queue.setdefault(body["identity"], {"sequence": len(queue) + 1, **body})
                result = {"sequence": queue[body["identity"]]["sequence"]}
            else:
                assert self.path == "/v1/runs"
                identity = body["coordinator_input_id"]
                assert body == {**queue[identity]["request"], "coordinator_input_id": identity}
                launched.append(identity)
                result = {"run_id": f"run_{len(launched)}"}
            if self.path == lost_path and not lost:
                lost = True
                self.close_connection = True
                return
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        binding = context.home / "bridge"
        binding.mkdir()
        (binding / "config.json").write_text(
            json.dumps(
                {"hermes_url": f"http://127.0.0.1:{server.server_port}", "service_key": "test"}
            )
        )
        context.env["SOFTMAX_BRIDGE_DIRECTORY"] = str(binding)
        with closing(open_storage(context.storage_path)) as conn, conn:
            conn.execute("INSERT INTO coordinator VALUES ('session', 'session-1')")
        event = {"wakeAgent": True, "context": {"pending": [{"kind": "champion_submission_poll"}]}}
        with pytest.raises(ConnectionError):
            wake(context, event)
        assert "run_id" in wake(context, event)
        assert len(queue) == 1 and len(set(launched)) == 1
        completed = True
        assert wake(context, event) == {"wakeAgent": False}
        assert len(queue) == 1
        with closing(open_storage(context.storage_path)) as conn:
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert tables == {"coordinator"}
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_card_events_wake_the_session_that_created_the_card(context, board_file):
    """#16: a card's events resume its origin chat, not whichever chat called a tool last."""
    inputs = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"pending": None}).encode())

        def do_POST(self):
            assert self.path == "/v1/coordinator/inputs"
            inputs.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"sequence": len(inputs)}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        binding = context.home / "bridge"
        binding.mkdir()
        (binding / "config.json").write_text(
            json.dumps(
                {"hermes_url": f"http://127.0.0.1:{server.server_port}", "service_key": "test"}
            )
        )
        context.env["SOFTMAX_BRIDGE_DIRECTORY"] = str(binding)
        with sqlite3.connect(board_file) as conn:
            conn.execute("UPDATE tasks SET session_id='chat-a' WHERE id='card-1'")
            conn.execute("INSERT INTO tasks VALUES ('card-2', 'done', 'card/2', '', 'chat-b')")
        with closing(open_storage(context.storage_path)) as conn, conn:
            conn.execute("INSERT INTO coordinator VALUES ('session', 'chat-latest')")
        card_1 = {"kind": "xp_terminal", "card": "card-1"}
        card_2 = {"kind": "credits_available", "card": "card-2"}
        no_origin = {"kind": "permit_answered", "card": "card-3"}
        poll = {"kind": "champion_submission_poll", "tick": "t"}
        pending = [card_1, poll, card_2, no_origin]
        wake(context, {"wakeAgent": True, "context": {"pending": pending}})
        routed = {}
        for body in inputs:
            prompt = body["request"]["input"]
            routed[body["request"]["session_id"]] = json.loads(prompt.split("\n", 1)[1])["pending"]
        assert routed == {
            "chat-a": [card_1],
            "chat-b": [card_2],
            "chat-latest": [poll, no_origin],
        }

        # #19: a league result resumes the session that made the submission, then is marked
        # followed up so the gate never wakes for that submission again.
        inputs.clear()
        remember_submission_session(context, "CS-1", "chat-submitter")
        result = {"kind": "league_result", "champion_submission": "CS-1"}
        unknown = {"kind": "league_result", "champion_submission": "CS-2"}
        assert league_results_seen(context) == set()
        wake(context, {"wakeAgent": True, "context": {"pending": [result, unknown]}})
        routed = {
            body["request"]["session_id"]: json.loads(body["request"]["input"].split("\n", 1)[1])[
                "pending"
            ]
            for body in inputs
        }
        assert routed == {"chat-submitter": [result], "chat-latest": [unknown]}
        assert league_results_seen(context) == {"CS-1", "CS-2"}
    finally:
        server.shutdown()
        worker.join()
        server.server_close()

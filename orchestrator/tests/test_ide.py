import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from orchestrator.ide import IDE


@pytest.mark.parametrize("credential", ["cga_fake", '{"token":"cga_fake","expires_at":"2099"}'])
def test_bridge_request_shapes(context, credential):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(
                (
                    self.command,
                    self.path,
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        do_PATCH = do_POST

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        context.env.update(
            COGAMES_API_URL=f"http://127.0.0.1:{server.server_port}/api",
            SOFTMAX_PLAYER_CREDENTIAL=credential,
        )
        ide = IDE(context)
        ide.ide_say("Finished", "say:task_events:3", "goal-1")
        patch = {"set": {"state": "done"}, "base_rev": 7, "idempotency_key": "task:7"}
        ide.ide_task_update("task/1", patch)
        assert seen == [
            (
                "POST",
                "/api/observatory/v2/cogents/ply_test/say",
                "Bearer cga_fake",
                {"text": "Finished", "idempotency_key": "say:task_events:3", "goal_id": "goal-1"},
            ),
            ("PATCH", "/api/observatory/v2/cogents/ply_test/c/tasks/task%2F1", "Bearer cga_fake", patch),
        ]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

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
            (
                "PATCH",
                "/api/observatory/v2/cogents/ply_test/c/tasks/task%2F1",
                "Bearer cga_fake",
                patch,
            ),
        ]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("text", "", False),
        ("text", "x" * 20_000, True),
        ("text", "x" * 20_001, False),
        ("key", "", False),
        ("key", "x" * 200, True),
        ("key", "x" * 201, False),
        ("key", "permit:reserved", False),
        ("key", "say:\x00", False),
        ("goal_id", "", True),
        ("goal_id", "x" * 200, True),
        ("goal_id", "x" * 201, False),
    ],
)
def test_say_exported_schema(field, value, valid):
    from jsonschema import Draft202012Validator

    from orchestrator.plugin import register

    class Registry:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **tool):
            self.tools[tool["name"]] = tool

        def register_middleware(self, kind, callback):
            pass

    registry = Registry()
    register(registry)
    schema = registry.tools["ide_say"]["schema"]["parameters"]
    arguments = {"text": "Finished", "key": "say:task_events:121", field: value}
    assert Draft202012Validator(schema).is_valid(arguments) is valid


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            [
                {
                    "loc": ["body", "text"],
                    "type": "string_too_long",
                    "msg": "do not echo secret",
                    "input": "secret",
                    "ctx": {"secret": "secret"},
                }
            ],
            {"validation": [{"field": "text", "type": "string_too_long"}]},
        ),
        (
            [{"loc": ["body", "secret"], "type": "secret", "input": "secret"}],
            {"validation": [{"field": "other", "type": "validation_error"}]},
        ),
        (
            "Idempotency key secret was already used for a different write",
            {"kind": "idempotency_key_reused"},
        ),
        ("unrecognized secret error", {}),
    ],
)
def test_say_validation_details_are_safe(context, monkeypatch, detail, expected):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    from orchestrator import plugin

    headers = Message()
    headers["Content-Type"] = "application/json"

    def rejected(*args):
        raise HTTPError(
            "http://localhost/say",
            422,
            "Unprocessable Entity",
            headers,
            BytesIO(json.dumps({"detail": detail}).encode()),
        )

    monkeypatch.setattr(plugin, "profile_context", lambda: context)
    monkeypatch.setattr(IDE, "request", rejected)
    result = plugin.handler("ide_say")({"text": "Finished", "key": "say:task_events:121"})
    assert json.loads(result)["error"]["details"] == {"status": 422, **expected}
    assert "secret" not in result


@pytest.mark.parametrize(
    ("status", "content_type"), [(422, "text/html"), (503, "application/json")]
)
def test_say_other_http_errors_keep_status(context, monkeypatch, status, content_type):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    from orchestrator import plugin

    headers = Message()
    headers["Content-Type"] = content_type

    def rejected(*args):
        raise HTTPError("http://localhost/say", status, "Error", headers, BytesIO(b"secret"))

    monkeypatch.setattr(plugin, "profile_context", lambda: context)
    monkeypatch.setattr(IDE, "request", rejected)
    result = plugin.handler("ide_say")({"text": "Finished", "key": "say:task_events:121"})
    assert json.loads(result)["error"]["details"] == {"status": status}
    assert "secret" not in result


def test_background_news_retries_keep_the_first_payload(context, monkeypatch):
    sent = []
    monkeypatch.setattr(
        IDE, "request", lambda self, method, path, payload: sent.append(payload) or {"seq": 123}
    )
    IDE(context).ide_say("Upload failed", "say:task_events:54", "gota-starter")
    IDE(context).ide_say("A different summary from a concurrent wake", "say:task_events:54")
    assert sent[0] == sent[1]



def test_refusals_say_what_they_mean():
    from orchestrator.evidence import refusal
    from orchestrator.workspace import WorkspaceError

    assert "not proof the evidence is absent" in refusal(403, "policy-versions?player_id=p")
    assert "no downloadable player file" in refusal(409, "policy-files/v1/download")
    unbound = WorkspaceError({"result": {"isError": True, "content": [{"text": "Conversation not found"}]}})
    assert "native Hermes session" in unbound.envelope["error"]["recovery"]
    other = WorkspaceError({"error": {"message": "bad scope"}})
    assert "native Hermes session" not in other.envelope["error"]["recovery"]

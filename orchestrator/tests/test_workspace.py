import json
from pathlib import Path

import pytest

from orchestrator.context import Context
from orchestrator.workspace import Workspace


def context(credential="cga_fixture"):
    return Context(
        Path("/unused"),
        {
            "SOFTMAX_IDE_URL": "https://softmax.com/ide-final",
            "SOFTMAX_PLAYER_CREDENTIAL": credential,
        },
    )


def test_uses_player_exchange_and_original_mcp_request(monkeypatch):
    calls = []

    def post(self, path, token, body):
        calls.append((path, token, body))
        if path.endswith("mcp-token"):
            return {"token": "short-lived"}
        return {"result": {"content": [{"type": "text", "text": "receipt"}]}}

    monkeypatch.setattr(Workspace, "post", post)
    client = Workspace(context(json.dumps({"token": "cga_fixture", "expires_at": "future"})))
    operation = {"method": "layout.read", "conversation_id": "spoof", "request_id": "spoof"}
    result = client.workspace_sdk("original-conversation", "original-request", operation)
    assert calls[0] == ("/hermes/mcp-token", "cga_fixture", {})
    assert calls[1][2]["method"] == "initialize"
    assert calls[-1][1] == "short-lived"
    args = calls[-1][2]["params"]["arguments"]
    assert args["conversation_id"] == "original-conversation"
    assert args["request_id"] == "original-request"
    assert calls[-1][2]["id"] == "original-request"
    assert result["result"]["content"][0]["text"] == "receipt"


def test_preserves_server_refusals(monkeypatch):
    def post(self, path, token, body):
        if path.endswith("mcp-token"):
            return {"token": "short-lived"}
        if body["method"] == "initialize":
            return {"result": {}}
        return {
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": '{"status":"forbidden"}'}],
            }
        }

    monkeypatch.setattr(Workspace, "post", post)
    result = Workspace(context()).workspace_sdk(
        "conversation", "request", {"method": "layout.read"}
    )
    assert result["result"]["isError"] is True


@pytest.mark.parametrize(
    "url",
    [
        "http://softmax.com/ide-final",
        "https://user:pass@softmax.com/ide-final",
        "https://softmax.com/ide-final?token=x",
    ],
)
def test_rejects_unsafe_endpoint(url):
    ctx = context()
    ctx.env["SOFTMAX_IDE_URL"] = url
    with pytest.raises(ValueError):
        Workspace(ctx)


def test_unconfigured_endpoint_is_explicit():
    ctx = context()
    del ctx.env["SOFTMAX_IDE_URL"]
    assert (
        Workspace(ctx).workspace_sdk("chat", "call", {"method": "layout.read"})["kind"]
        == "unavailable"
    )

import json
from pathlib import Path

import pytest

from orchestrator.context import Context
from orchestrator.workspace import Workspace, WorkspaceError


def context(credential="cga_fixture"):
    return Context(
        Path("/unused"),
        {
            "COGAMES_API_URL": "https://softmax.com/api",
            "SOFTMAX_PLAYER_CREDENTIAL": credential,
        },
    )


def test_uses_player_identity_and_original_mcp_request(monkeypatch):
    calls = []

    def post(self, path, token, body):
        calls.append((path, token, body))
        return {"result": {"content": [{"type": "text", "text": "receipt"}]}}

    monkeypatch.setattr(Workspace, "post", post)
    client = Workspace(context(json.dumps({"token": "cga_fixture", "expires_at": "future"})))
    operation = {"method": "layout.read", "conversation_id": "spoof", "request_id": "spoof"}
    result = client.ide_workspace("original-request", operation)
    assert all(path == "/mcp" for path, _, _ in calls)
    assert calls[0][2]["method"] == "initialize"
    assert calls[-1][1] == "cga_fixture"
    args = calls[-1][2]["params"]["arguments"]
    assert "conversation_id" not in args
    assert args["operation"] == operation
    assert args["request_id"] == "original-request"
    assert calls[-1][2]["id"] == "original-request"
    assert result["result"]["content"][0]["text"] == "receipt"


def test_preserves_server_refusals(monkeypatch):
    def post(self, path, token, body):
        if body["method"] == "initialize":
            return {"result": {}}
        return {
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": '{"status":"forbidden"}'}],
            }
        }

    monkeypatch.setattr(Workspace, "post", post)
    with pytest.raises(WorkspaceError) as error:
        Workspace(context()).ide_workspace("request", {"method": "layout.read"})
    assert error.value.envelope["ok"] is False
    assert error.value.envelope["error"]["details"]["result"]["isError"] is True


@pytest.mark.parametrize("stage", ["initialize", "tools/call"])
def test_protocol_error_reaches_plugin_as_failure(monkeypatch, stage):
    from orchestrator import plugin

    calls = []
    refusal = {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid tool arguments"}}

    def post(self, path, token, body):
        calls.append(body["method"])
        return refusal if body["method"] == stage else {"result": {}}

    monkeypatch.setattr(Workspace, "post", post)
    monkeypatch.setattr(plugin, "profile_context", context)
    result = json.loads(plugin.handler("ide_workspace")({
        "request_id": "original", "operation": {"method": "league.overview", "leagueId": "unsupported"},
    }))
    assert result["ok"] is False
    assert result["error"]["reason"] == "workspace_protocol"
    assert result["error"]["details"] == refusal
    if stage == "initialize":
        assert calls == ["initialize"]


@pytest.mark.parametrize(
    "url",
    [
        "http://softmax.com/ide",
        "https://user:pass@softmax.com/ide",
        "https://softmax.com/ide?token=x",
    ],
)
def test_rejects_unsafe_endpoint(url):
    ctx = context()
    ctx.env["COGAMES_API_URL"] = url
    with pytest.raises(ValueError):
        Workspace(ctx)


def test_endpoint_uses_existing_platform_origin():
    ctx = context()
    ctx.env["COGAMES_API_URL"] = "https://preview.softmax.com:8443/api/"
    assert Workspace(ctx).base == "https://preview.softmax.com:8443/ide"


def test_default_platform_endpoint():
    ctx = context()
    del ctx.env["COGAMES_API_URL"]
    assert Workspace(ctx).base == "https://softmax.com/ide"


def test_discovers_authenticated_deployed_contract(monkeypatch):
    calls = []
    schema = {
        "jsonrpc": "2.0",
        "id": "workspace-schema",
        "result": {
            "tools": [
                {
                    "name": "ide_workspace",
                    "description": "Deployed instructions",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "object",
                                "required": ["leagueId"],
                                "properties": {"method": {"const": "projects.fileUpload"}},
                            }
                        },
                    },
                }
            ]
        },
    }

    def post(self, path, token, body):
        calls.append((path, token, body))
        return schema if body["method"] == "tools/list" else {"result": {}}

    monkeypatch.setattr(Workspace, "post", post)
    result = Workspace(context()).ide_workspace_schema()
    assert result == schema
    assert [body["method"] for _, _, body in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert all((path, token) == ("/mcp", "cga_fixture") for path, token, _ in calls)


@pytest.mark.parametrize("method", ["ide_workspace_schema", "ide_workspace"])
def test_initialization_failure_stops_request(monkeypatch, method):
    calls = []
    error = {"code": -32603, "message": "Identity verification unavailable"}

    def post(self, path, token, body):
        calls.append(body["method"])
        return {"error": error}

    monkeypatch.setattr(Workspace, "post", post)
    client = Workspace(context())
    args = () if method == "ide_workspace_schema" else ("request", {"method": "layout.read"})
    with pytest.raises(WorkspaceError) as failure:
        getattr(client, method)(*args)
    assert failure.value.envelope["error"]["details"] == {"error": error}
    assert calls == ["initialize"]

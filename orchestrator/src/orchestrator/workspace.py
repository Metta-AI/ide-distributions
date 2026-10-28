"""The hosted IDE's shared workspace SDK, reached through its MCP endpoint."""

import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .context import Context

# The desktop client talks to Hermes sessions directly; they are not web IDE conversations.
UNBOUND_CONVERSATION = (
    " 'Conversation not found' means this chat (a native Hermes session, e.g. from the desktop"
    " client) is not registered as a web IDE conversation. It is not missing policy source, a"
    " refused submission or a lost chat: continue with tools that do not need that binding"
    " (upload_build, the submit tools, policy_versions)."
)


class WorkspaceError(RuntimeError):
    def __init__(self, response):
        self.envelope = {
            "ok": False,
            "error": {
                "reason": "workspace_protocol" if "error" in response else "workspace_tool",
                "message": "The workspace server rejected the operation",
                "details": response,
                "recovery": (
                    "Read ide_workspace_schema for supported arguments. Do not retry unsupported "
                    "scope or invent parameters. For uncertain effects, inspect the original request_id."
                    + (UNBOUND_CONVERSATION if "conversation not found" in json.dumps(response).lower() else "")
                ),
            },
        }
        super().__init__(self.envelope["error"]["message"])


class Workspace:
    def __init__(self, context: Context):
        url = urlsplit(context.env.get("COGAMES_API_URL", "https://softmax.com/api"))
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "COGAMES_API_URL must be an HTTPS URL without credentials, query or fragment"
            )
        self.base = f"{url.scheme}://{url.netloc}/ide"
        credential = context.env["SOFTMAX_PLAYER_CREDENTIAL"]
        self.token = json.loads(credential)["token"] if credential.startswith("{") else credential

    def post(self, path, token, body):
        request = Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "ide-orchestrator/0.1",
            },
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            return None if response.status == 202 else json.load(response)

    def initialized_request(self, request):
        initialized = self.post(
            "/mcp",
            self.token,
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "ide-orchestrator", "version": "0.1.0"},
                },
            },
        )
        if "error" in initialized:
            raise WorkspaceError(initialized)
        self.post("/mcp", self.token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = self.post("/mcp", self.token, request)
        if "error" in result or result.get("result", {}).get("isError", False):
            raise WorkspaceError(result)
        return result

    def ide_workspace_schema(self):
        """Discover the deployed SDK contract rather than copying its schemas into this distribution."""
        return self.initialized_request(
            {"jsonrpc": "2.0", "id": "workspace-schema", "method": "tools/list"}
        )

    def ide_workspace(self, request_id, operation):
        # Envelope fields cannot be overridden by operation data supplied by the model.
        return self.initialized_request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "ide_workspace",
                    "arguments": {
                        "operation": operation,
                        "request_id": request_id,
                    },
                },
            }
        )

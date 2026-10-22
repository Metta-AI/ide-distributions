"""The hosted IDE's shared workspace SDK, reached through its MCP endpoint."""

import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .context import Context


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
            return {"kind": "protocol_error", "error": initialized["error"]}
        self.post("/mcp", self.token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.post("/mcp", self.token, request)

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

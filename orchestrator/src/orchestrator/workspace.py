"""The hosted IDE's shared workspace SDK, reached through its MCP endpoint."""

import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .context import Context


class Workspace:
    def __init__(self, context: Context):
        self.base = context.env.get("SOFTMAX_IDE_URL", "").rstrip("/")
        url = urlsplit(self.base)
        if self.base and (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("SOFTMAX_IDE_URL must be an HTTPS application URL")
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
            },
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            return None if response.status == 202 else json.load(response)

    def workspace_sdk(self, conversation_id, request_id, operation):
        if not self.base:
            return {
                "kind": "unavailable",
                "reason": "This profile has no configured SOFTMAX_IDE_URL.",
            }
        # Refresh through the supported player-token exchange. No owner credential is installed.
        grant = self.post("/hermes/mcp-token", self.token, {})
        bearer = grant["token"]
        initialized = self.post(
            "/hermes/mcp",
            bearer,
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
        self.post("/hermes/mcp", bearer, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        # Envelope fields cannot be overridden by operation data supplied by the model.
        result = self.post(
            "/hermes/mcp",
            bearer,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "workspace_sdk",
                    "arguments": {
                        **operation,
                        "conversation_id": conversation_id,
                        "request_id": request_id,
                    },
                },
            },
        )
        return result

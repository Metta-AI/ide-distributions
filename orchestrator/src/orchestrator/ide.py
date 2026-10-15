"""The bridge's authenticated cogents request shapes, using stdlib HTTP."""

import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from .context import Context


class IDE:
    def __init__(self, context: Context):
        env = context.env
        credential = env["SOFTMAX_PLAYER_CREDENTIAL"]
        self.token = json.loads(credential)["token"] if credential.startswith("{") else credential
        self.base = env.get("COGAMES_API_URL", "https://softmax.com/api").rstrip("/")
        self.player = quote(env["RESEARCH_PLAYER_ID"], safe="")

    def request(self, method, path, payload=None):
        request = Request(
            f"{self.base}/v2/cogents/{self.player}/{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "ide-orchestrator/0.1",
            },
        )
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}

    def ide_say(self, text, key, goal_id=None):
        payload = {"text": text, "idempotency_key": key}
        if goal_id is not None:
            payload["goal_id"] = goal_id
        return self.request("POST", "say", payload)

    def ide_task_update(self, task_id, patch):
        """Patch carries set, base_rev and idempotency_key as in the bridge."""
        return self.request("PATCH", "c/tasks/" + quote(task_id, safe=""), patch)

    def permit(self, ask):
        return self.request("GET", "c/asks/" + quote(ask, safe=""))

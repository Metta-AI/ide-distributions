"""A scripted stand-in for the Preston-role agent (Runtime 9.3 contract tests).

It touches the system only through ``autoresearch comms ...`` subprocesses, as an external
reader with read access to the workspace would. Each instance is one consumer with its own
cursor."""

import json
import subprocess
import sys
from pathlib import Path


class CommsError(RuntimeError):
    def __init__(self, payload: dict, code: int):
        super().__init__(f"exit {code}: {payload}")
        self.payload = payload
        self.code = code


def run_comms(env: dict[str, str], *args: str) -> dict:
    """Run ``autoresearch comms <args>`` and return the ``data`` of its JSON envelope."""
    result = subprocess.run(
        [sys.executable, "-m", "autoresearch", "comms", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise CommsError({"stdout": result.stdout, "stderr": result.stderr}, result.returncode)
    if result.returncode != 0 or not payload["ok"]:
        raise CommsError(payload, result.returncode)
    return payload["data"]


class FakePreston:
    def __init__(self, consumer: str, canonical: Path, env: dict[str, str]):
        self.consumer = consumer
        self.env = env | {"AUTORESEARCH_WORKSPACE": str(canonical)}

    def comms(self, *args: str) -> dict:
        return run_comms(self.env, *args)

    def read(self) -> list[dict]:
        """New messages since this consumer's cursor; the cursor then moves past them."""
        data = self.comms("list", "--consumer", self.consumer, "--advance")
        return data["messages"]

    def resolve(self, uri: str, rev: str) -> dict:
        return self.comms("resolve", uri, "--rev", rev)

    def resolve_all(self, message: dict) -> list[dict]:
        return [self.resolve(ref["uri"], ref["rev"]) for ref in message["refs"]]

    def get(self, key: str | int) -> dict:
        return self.comms("get", str(key))

    def status(self, message_id: str) -> dict:
        return self.comms("status", message_id)

    def send(self, kind: str, **payload) -> str:
        data = self.comms("inbound", kind, "--json", json.dumps(payload), "--author", self.consumer)
        return data["id"]

    def reply(self, in_reply_to: str, text: str) -> str:
        return self.send("reply", in_reply_to=in_reply_to, text=text)

    def seen(self, *message_ids: str) -> str:
        return self.send("seen", ids=list(message_ids))

    def task(self, title: str, mode: str, why: str, refs: list[str], priority: str) -> str:
        return self.send("task", title=title, mode=mode, why=why, refs=refs, priority=priority)

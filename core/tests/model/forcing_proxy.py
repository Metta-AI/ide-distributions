"""A local stand-in for OpenRouter that makes a Session's first calls refused ones.

The runs API can only continue a stored history with a new user message, so it cannot put a real
Session in the middle of a turn after refused calls. This proxy can: Hermes talks to it as its
OpenRouter base URL, it answers a conversation's first requests itself with the broken calls a
script names, and forwards everything else unchanged. So the refusals are real (the real tool, the
real hooks, Hermes's real loop guard), the turn is one turn, and the model's next call is real.

It listens under a name in OpenRouter's domain (``PROXY_HOST``, which the docker command in the
README points at the container itself), because Hermes shapes a request by its base URL: only for
OpenRouter does it add cache markers (``agent/agent_runtime_helpers.py``, which turn the last results
into text parts) and replay ``reasoning_details`` (``agent/reasoning_params.py``). So what the model
gets is what production sends.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://openrouter.ai/api/v1"
PROXY_HOST = "proxy.openrouter.ai"
FORCED_ID_PREFIX = "call_forced_"


@dataclass(frozen=True)
class ForcedCall:
    name: str
    arguments: dict
    reasoning: str = ""


@dataclass
class ForcingProxy:
    """``scripts`` maps a conversation's first user message to the calls to force, in order."""

    key: str = field(repr=False)
    scripts: dict[str, list[ForcedCall]] = field(default_factory=dict)
    requests: list[dict] = field(default_factory=list)  # every chat request, as Hermes sent it
    server: ThreadingHTTPServer | None = None
    answered: Counter[str] = field(default_factory=Counter)  # script key -> calls forced so far
    lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> str:
        proxy = self
        address = socket.gethostbyname(PROXY_HOST)
        assert address == "127.0.0.1", f"run with docker's --add-host {PROXY_HOST}:127.0.0.1"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                proxy._forward(self, None)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                proxy._on_post(self, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://{PROXY_HOST}:{self.server.server_address[1]}/api/v1"

    def stop(self) -> None:
        self.server.shutdown()

    def next_forced(self, messages: list[dict]) -> tuple[ForcedCall, int] | None:
        """The conversation's next scripted call and its position, if any are left. Counted here,
        not from the request: the plugin's middleware drops repeated refused calls from it."""
        first_user = next((m for m in messages if m.get("role") == "user"), {})
        content = first_user.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        script_key = next((key for key in self.scripts if key in text), None)
        if script_key is None:
            return None
        with self.lock:
            index = self.answered[script_key]
            if index == len(self.scripts[script_key]):
                return None
            self.answered[script_key] += 1
        return self.scripts[script_key][index], index

    def _on_post(self, handler: BaseHTTPRequestHandler, raw: bytes) -> None:
        if not handler.path.endswith("/chat/completions"):
            self._forward(handler, raw)
            return
        body = json.loads(raw)
        # Only the main loop offers tools: titling and other side requests are forwarded.
        if body.get("tools"):
            self.requests.append(body)
            forced = self.next_forced(body.get("messages") or [])
            if forced is not None:
                self._answer_with(handler, *forced, body)
                return
        self._forward(handler, raw)

    def _answer_with(
        self, handler: BaseHTTPRequestHandler, call: ForcedCall, index: int, body: dict
    ) -> None:
        tool_call = {
            "index": 0,
            "id": f"{FORCED_ID_PREFIX}{index + 1}",
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
        }
        message: dict = {"role": "assistant", "content": "", "tool_calls": [tool_call]}
        if call.reasoning:
            message["reasoning"] = call.reasoning
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0}
        base = {"id": f"forced-{index}", "model": body.get("model"), "provider": "forced"}
        if body.get("stream"):
            chunks = [
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": message, "finish_reason": None}],
                },
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": usage,
                },
            ]
            payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            self._send(handler, 200, "text/event-stream", payload.encode())
            return
        reply = {
            **base,
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
            "usage": usage,
        }
        self._send(handler, 200, "application/json", json.dumps(reply).encode())

    def _send(
        self, handler: BaseHTTPRequestHandler, status: int, content_type: str, data: bytes
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _forward(self, handler: BaseHTTPRequestHandler, raw: bytes | None) -> None:
        path = handler.path.split("/api/v1", 1)[-1]
        request = urllib.request.Request(
            f"{UPSTREAM}{path}",
            data=raw,
            method="POST" if raw is not None else "GET",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "User-Agent": "ide-model-tests",
            },
        )
        try:
            response = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            handler.send_response(response.status)
            handler.send_header(
                "Content-Type", response.headers.get("Content-Type", "application/json")
            )
            handler.send_header("Connection", "close")
            handler.end_headers()
            while chunk := response.read(4096):
                handler.wfile.write(chunk)
                handler.wfile.flush()
        handler.close_connection = True

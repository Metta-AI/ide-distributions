"""A local stand-in for OpenRouter that makes a Session's first calls refused ones.

The runs API can only continue a stored history with a new user message, so it cannot put a real
Session in the middle of a turn after refused calls. This proxy can: Hermes talks to it as its
OpenRouter base URL, it answers a conversation's first requests itself with the broken calls a
script names, and forwards everything else unchanged. So the refusals are real (the real tool, the
real hooks, Hermes's real loop guard), the turn is one turn, and the model's next call is real.

What a base URL other than OpenRouter's changes in Hermes (``agent/reasoning_params.py``): it stops
sending ``reasoning`` and replaying ``reasoning_details``. The proxy puts ``reasoning`` back as
production sends it; the replayed details stay out, though ``reasoning`` and ``reasoning_content``
are still replayed. A captured request also had no ``max_tokens``, so the plugin's output cap
(``bound_output_request``) did not apply; that only changes the credit a request reserves.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.model.harness import HERMES_DEFAULT_REASONING

UPSTREAM = "https://openrouter.ai/api/v1"
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

    def start(self) -> str:
        proxy = self

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
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"

    def stop(self) -> None:
        self.server.shutdown()

    def _script_for(self, messages: list[dict]) -> list[ForcedCall]:
        first_user = next((m for m in messages if m.get("role") == "user"), {})
        content = first_user.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        return next((calls for key, calls in self.scripts.items() if key in text), [])

    def _on_post(self, handler: BaseHTTPRequestHandler, raw: bytes) -> None:
        if not handler.path.endswith("/chat/completions"):
            self._forward(handler, raw)
            return
        body = json.loads(raw)
        messages = body.get("messages") or []
        script = self._script_for(messages)
        # Only the main loop offers tools: titling and other side requests are forwarded.
        forced_so_far = sum(
            str(m.get("tool_call_id", "")).startswith(FORCED_ID_PREFIX) for m in messages
        )
        if body.get("tools"):
            self.requests.append(body)
        if body.get("tools") and forced_so_far < len(script):
            self._answer_with(handler, script[forced_so_far], forced_so_far, body)
            return
        if body.get("tools"):
            raw = json.dumps({**body, "reasoning": HERMES_DEFAULT_REASONING}).encode()
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

"""Manual packaged proof: a handled external wait must permit idle without archiving its card.

Build the ordinary Metta autoresearch image from this distribution commit, then invoke:
  docker run --rm --init --network none \
    --tmpfs /opt/data:exec,uid=10000,gid=10000,mode=0700 \
    --tmpfs /run:uid=10000,gid=10000,mode=0700 \
    --entrypoint /opt/hermes/.venv/bin/python IMAGE \
    /opt/ide-distributions/orchestrator/tests/system/wait_retirement_system.py

Hermes, profiles, native Kanban, the coordinator inbox and the relay lifecycle are real.
Only external model, credential management and credit-balance responses are scripted.
This is explicit functional verification; do not add it to image-build or PR gates.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from observatory_core.hermes.definitions import HostedRuntimeDefinition
from observatory_hosted_worker.relay.config import RelayRuntime
from observatory_hosted_worker.relay.lifecycle import activity, drain

HOME = Path("/opt/data")
KEY = "background-proof-service-key"
READY = threading.Event()
CREDITS_POLLED = threading.Event()
COMPLETED = threading.Event()
RESUMED = threading.Event()
HANDOFF = threading.Event()
CARD = ""


class Platform(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.endswith("/inference"):
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            self.reply(
                {"key": "fixture", "expires_at": future, "renew_at": future, "allowance_usd": 1}
            )
        else:
            assert self.path.endswith("/bridge"), self.path
            READY.set()
            self.reply({})

    def do_GET(self):
        if self.path.endswith("/connection"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"\n")
            self.wfile.flush()
            time.sleep(30)
        elif self.path.endswith("/usage/me/credits"):
            CREDITS_POLLED.set()
            self.reply({"status": {"balance_credits": 10 if COMPLETED.is_set() else 0}})
        else:
            self.send_error(404, self.path)

    def do_POST(self):
        global CARD
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path.endswith("/chat/completions"), self.path
        terminal = "credits_available" in json.dumps(body["messages"])
        if terminal:
            assert COMPLETED.is_set(), "coordinator resumed before the external job completed"
            RESUMED.set()
        offered = {item["function"]["name"] for item in body.get("tools", [])}
        prior = [
            call["function"]
            for message in body["messages"]
            for call in message.get("tool_calls", [])
        ]
        called = {call["name"] for call in prior}
        called.update(
            entry["name"]
            for call in prior
            if call["name"] == "tool_call"
            for entry in json.loads(call["arguments"])["calls"]
        )
        described = {
            name
            for call in prior
            if call["name"] == "tool_describe"
            for name in json.loads(call["arguments"])["names"]
        }
        message = {
            "role": "assistant",
            "content": "Credits available" if terminal else "Waiting for credits",
        }
        tool = None
        arguments = {}
        if offered and body["model"] == "fixture-researcher":
            if "terminal" not in called:
                tool = "terminal"
                arguments = {"command": '"$RESEARCH_CLI" softmax credits'}
            elif "kanban_complete" not in called:
                assert "credits" in json.dumps(body["messages"]), (
                    "researcher did not observe credit status"
                )
                tool = "kanban_complete"
                arguments = {
                    "summary": "Credits unavailable; resume when funded",
                    "metadata": {"awaiting": {"credits": True}},
                }
                HANDOFF.set()
        elif offered and not terminal and "background-proof-start" in json.dumps(body["messages"]):
            if "card_create" not in called:
                tool = "card_create"
                arguments = {
                    "title": "Wait for credits",
                    "assignee": "researcher-1",
                    "command_id": "wait-retirement-proof:credits",
                    "body": "Read credits with the installed research CLI. Complete this launch-only "
                    "assignment with awaiting.credits metadata while credits are unavailable.",
                }
            else:
                result = next(item for item in reversed(body["messages"]) if item["role"] == "tool")
                CARD = json.loads(result["content"])["data"]["card"]["id"]
        calls = []
        if tool is not None:
            # Follow the installed discovery bridge; never enable deferred tools in test config.
            if tool not in offered:
                assert {"tool_describe", "tool_call"} <= offered, (tool, offered)
                if tool not in described:
                    tool, arguments = "tool_describe", {"names": [tool]}
                else:
                    tool, arguments = (
                        "tool_call",
                        {"calls": [{"name": tool, "arguments": arguments}]},
                    )
            calls = [
                {
                    "id": "proof-" + tool,
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)},
                }
            ]
            message = {"role": "assistant", "content": None, "tool_calls": calls}
        completion = {
            "id": "proof",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "fixture",
            "choices": [
                {"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        if body.get("stream", False):
            chunk = completion | {
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": message
                        | (
                            {
                                "tool_calls": [
                                    call | {"index": index} for index, call in enumerate(calls)
                                ]
                            }
                            if tool
                            else {}
                        ),
                        "finish_reason": None,
                    }
                ],
            }
            finish = chunk | {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}
                ]
            }
            data = (
                "data: "
                + json.dumps(chunk)
                + "\n\ndata: "
                + json.dumps(finish)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.reply(completion)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Platform)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    selected = HostedRuntimeDefinition.model_validate_json(
        subprocess.check_output(
            [
                sys.executable,
                "/opt/autoresearch-runtime/runtime_definition.py",
                "--image",
                "sha256:" + "a" * 64,
                "--model",
                "fixture",
                "--researchers",
                "1",
            ]
        )
    ).model_copy(update={"relay": True})
    selected = selected.model_copy(
        update={
            "profiles": [
                profile.model_copy(
                    update={
                        "model": "fixture-researcher"
                        if profile.name.startswith("researcher-")
                        else "fixture",
                        "env": profile.env
                        | {
                            "OPENROUTER_BASE_URL": url + "/v1",
                            "OPENROUTER_MODEL": "fixture-researcher"
                            if profile.name.startswith("researcher-")
                            else "fixture",
                            "SOFTMAX_HERMES_TRACING": "0",
                        },
                    }
                )
                for profile in selected.profiles
            ]
        }
    )
    config = {
        "runtime": selected.model_dump(mode="json"),
        "bridge": {
            "player_id": "ply_proof",
            "agent": "hermes-00000000000040008000000000000111",
            "softmax_url": url,
            "hermes_url": "http://127.0.0.1:8642/p/orchestrator/",
            "player_token": "cga_proof",
            "service_key": KEY,
            "session_id": "proof",
            "bundle_id": "proof",
            "kanban_profile": "orchestrator",
        },
    }
    Path("/run/softmax").mkdir(exist_ok=True)
    Path("/run/softmax/runtime.json").write_text(json.dumps(config))
    os.environ.update(
        {
            "HERMES_HOME": str(HOME),
            "HERMES_REAL_HOME": str(HOME),
            "OPENROUTER_BASE_URL": url + "/v1",
        }
    )
    log = (HOME / "background-proof.log").open("w+")
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-m", "observatory_hosted_worker.relay.runtime"], stdout=log, stderr=log
    )
    try:
        # The runtime prepares each selected profile serially with its own 300-second deadline.
        deadline = time.monotonic() + 300 * len(selected.profiles)
        while not READY.wait(0.2):
            assert process.poll() is None, "relay exited"
            assert time.monotonic() < deadline, "startup deadline"
        print(
            json.dumps({"relay_startup_seconds": round(time.monotonic() - started, 3)}), flush=True
        )
        runtime = RelayRuntime.model_validate(config)
        deadline = time.monotonic() + 60
        while not (empty := activity(runtime, planned=False)).idle:
            assert time.monotonic() < deadline, empty
            time.sleep(0.2)
        print("PASS packaged empty profile permits automatic sleep", flush=True)
        # Submit the assignment through the gateway. The coordinator delegates a native card;
        # its installed researcher executes the CLI and emits the public kanban_complete handoff.
        with httpx.Client(
            base_url=config["bridge"]["hermes_url"],
            headers={"Authorization": "Bearer " + KEY},
            timeout=60,
        ) as api:
            response = api.post(
                "v1/runs",
                json={"session_id": "background-proof", "input": "background-proof-start"},
            )
            response.raise_for_status()
            assert HANDOFF.wait(150), "packaged researcher did not complete its launch assignment"
            # Native completion callbacks may still be running; observe their drain before testing the durable wait.
            deadline = time.monotonic() + 60
            while not (planned := activity(runtime, planned=True)).idle:
                assert time.monotonic() < deadline, planned
                time.sleep(0.2)
            # Automatic sleep rechecks activity after drain; the durable wait must survive native pause.
            drain(runtime, enabled=True)
            assert activity(runtime, planned=True).idle
            # Browser/client is absent throughout the idle interval; only local lifecycle reads remain.
            waiting = activity(runtime, planned=False)
            assert not waiting.idle and "orchestrator pending work (1)" in waiting.waiting_on, (
                waiting
            )
            print(
                "PASS public researcher handoff vetoes autosleep after drain without browser activity",
                flush=True,
            )
            drain(runtime, enabled=False)
            # Leave enough time to cross the native inbox's recovery admission delay; cron runs normally.
            assert CREDITS_POLLED.wait(150), (
                "installed supervisor never observed the unavailable credits"
            )
            assert not RESUMED.is_set()
            assert not activity(runtime, planned=False).idle
            # A later chat must not steal the pending researcher's continuation from its origin.
            api.post(
                "v1/runs",
                json={"session_id": "background-other", "input": "background-proof-other"},
            ).raise_for_status()
            deadline = time.monotonic() + 60
            while True:
                other = api.get("v1/runs", params={"session_id": "background-other", "limit": 20})
                other.raise_for_status()
                other_before = other.json()["data"]
                if other_before and all(entry["status"] == "completed" for entry in other_before):
                    break
                assert time.monotonic() < deadline, other.text
                time.sleep(0.2)
            COMPLETED.set()
            assert RESUMED.wait(150), "installed supervisor did not resume after funding"
            deadline = time.monotonic() + 60
            while True:
                runs = api.get("v1/runs", params={"session_id": "background-proof", "limit": 20})
                runs.raise_for_status()
                completed = [
                    entry
                    for entry in runs.json()["data"]
                    if "credits_available" in entry["input"] and entry["status"] == "completed"
                ]
                if completed:
                    result = api.get(f"v1/runs/{completed[-1]['run_ids'][-1]}")
                    result.raise_for_status()
                    assert result.json()["output"] == "Credits available", result.text
                    break
                assert time.monotonic() < deadline, runs.text
                time.sleep(0.2)
            other_after = api.get("v1/runs", params={"session_id": "background-other", "limit": 20})
            other_after.raise_for_status()
            assert other_after.json()["data"] == other_before, (
                "external completion changed the later chat"
            )
            assert CARD, "coordinator did not receive the created card"
            with sqlite3.connect(HOME / "kanban/boards/autoresearch/kanban.db") as db:
                assert (
                    db.execute("SELECT status FROM tasks WHERE id=?", (CARD,)).fetchone()[0]
                    == "done"
                )
                metadata = db.execute(
                    "SELECT metadata FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (CARD,),
                ).fetchone()[0]
                assert json.loads(metadata)["awaiting"]["credits"] is True
            deadline = time.monotonic() + 60
            while not activity(runtime, planned=False).idle:
                assert time.monotonic() < deadline, "workspace did not return to idle"
                time.sleep(0.2)
            print(
                "PASS external completion reaches native coordinator; handled work returns to idle",
                flush=True,
            )
    finally:
        process.terminate()
        process.wait(timeout=30)
        log.seek(0)
        print(log.read()[-6000:], flush=True)
        server.shutdown()
    print("BACKGROUND WAIT CONTRACT PASSED", flush=True)


if __name__ == "__main__":
    main()

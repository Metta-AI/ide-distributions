"""Native AIDE JSON-RPC transport and application projection."""

import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, model_validator
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core/src"))
from research_core.aide_messages import supported_messages  # noqa: E402

log = logging.getLogger(__name__)


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["workspace", "coworld"]
    coworld: str | None
    league: str | None

    @model_validator(mode="after")
    def coherent(self):
        if self.scope == "workspace" and self.coworld is None and self.league is None:
            return self
        if self.scope == "coworld" and self.coworld and self.league:
            return self
        raise ValueError("Invalid session scope")


class Application:
    def __init__(self, seam: httpx.AsyncClient, gateway: httpx.AsyncClient):
        self.seam, self.gateway = seam, gateway
        document = json.loads(
            (Path(__file__).resolve().parents[1] / "contract/aide/1.0.0/asyncapi.json").read_text()
        )
        self.schema = {"components": document["components"]}
        self.max_message_bytes = document["components"]["schemas"]["frontendBinding"]["properties"][
            "binding"
        ]["properties"]["max_message_bytes"]["const"]

    @staticmethod
    def size(message):
        return len(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode())

    def validate(self, name, value):
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}", **self.schema}, format_checker=FormatChecker()
        ).validate(value)

    async def execute(self, request):
        self.validate("Request", request)
        if request["method"] == "chat.send":
            response = await self.seam.post(
                "/aide-command",
                json={
                    "request_id": request["id"],
                    "method": "chat.send",
                    "params": request["params"],
                },
            )
            response.raise_for_status()
            receipt = response.json()
            if receipt["state"] == "recorded":
                self.validate("chat_sendResult", receipt["result"])
                return receipt["result"]
            if receipt["state"] == "id_reused":
                return {
                    "error": {
                        "code": -32000,
                        "message": "Request identity was reused",
                        "data": {"code": "id_reused"},
                    }
                }
        command = request["method"] in {
            "session.open",
            "chat.cancel",
            "task.cancel",
            "input.respond",
        }
        identity = {
            "request_id": request["id"],
            "method": request["method"],
            "params": request["params"],
        }
        if command:
            response = await self.seam.post("/aide-command", json=identity)
            response.raise_for_status()
            receipt = response.json()
            if receipt["state"] == "recorded":
                result = receipt["result"]
                if "error" in result:
                    self.validate("Error", {"jsonrpc": "2.0", "id": request["id"], **result})
                else:
                    self.validate(request["method"].replace(".", "_") + "Result", result)
                return result
            if receipt["state"] == "id_reused" or (
                receipt["state"] == "unknown" and request["method"] != "session.open"
            ):
                return {
                    "error": {
                        "code": -32000,
                        "message": "Request outcome is unresolved"
                        if receipt["state"] == "unknown"
                        else "Request identity was reused",
                        "data": {"code": "unavailable", "outcome": "unknown"}
                        if receipt["state"] == "unknown"
                        else {"code": "id_reused"},
                    }
                }
        result = await self.execute_validated(request)
        if request["method"] == "state.list" and "items" in result:
            params = request["params"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {k: v for k, v in params.items() if k not in {"limit", "cursor"}},
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:16]
            offset = 0
            if "cursor" in params:
                parts = params["cursor"].split(":")
                if (
                    len(parts) != 3
                    or parts[0] != str(result["revision"])
                    or parts[1] != fingerprint
                    or not parts[2].isdigit()
                ):
                    return {
                        "error": {
                            "code": -32000,
                            "message": "Snapshot cursor expired",
                            "data": {"code": "snapshot_expired"},
                        }
                    }
                offset = int(parts[2])
            limit = params.get("limit", 50)
            items = result["items"]
            result["items"] = items[offset : offset + limit]
            result["next_cursor"] = (
                f"{result['revision']}:{fingerprint}:{offset + limit}"
                if offset + limit < len(items)
                else None
            )
        if "error" in result:
            self.validate("Error", {"jsonrpc": "2.0", "id": request["id"], **result})
        else:
            self.validate(request["method"].replace(".", "_") + "Result", result)
        if (
            self.size({"jsonrpc": "2.0", "id": request["id"], "result": result})
            > self.max_message_bytes
        ):
            result = {
                "error": {
                    "code": -32000,
                    "message": "AIDE result exceeds the message limit",
                    "data": {"code": "too_large"},
                }
            }
        if command:
            response = await self.seam.post("/aide-command", json={**identity, "result": result})
            response.raise_for_status()
        return result

    async def snapshot(self, scope: Scope | None = None, session_id: str | None = None):
        params = {"store": "workspace"}
        if scope is not None:
            params.update({"coworld": scope.coworld, "league": scope.league})
        if session_id is not None:
            params["session_id"] = session_id
        result = await self.seam.get(
            "/snapshot", params={k: v for k, v in params.items() if v is not None}
        )
        if result.is_success:
            body = result.json()
            revision = body["seq"]
            for row in body["records"]["sessions"]:
                pending = [
                    item for item, _, _ in await self.pending(row, body["records"]["admissions"])
                ]
                changed = await self.seam.post(
                    "/aide-pending", json={"session_id": row["id"], "pending": pending}
                )
                changed.raise_for_status()
                revision = max(revision, changed.json()["revision"])
            if revision != body["seq"]:
                result = await self.seam.get(
                    "/snapshot", params={k: v for k, v in params.items() if v is not None}
                )
        return result

    @staticmethod
    def session(row, admissions):
        context = row["session_context"] or {}
        return {
            "id": row["id"],
            "parent_session_id": row["parent"],
            "started_at": row["started"],
            "ended_at": row["ended"],
            "scope": {
                "scope": "coworld" if row["coworld_id"] else "workspace",
                "coworld": row["coworld_id"],
                "league": context.get("league"),
            },
            "active_request_ids": [
                a["op_id"]
                for a in admissions
                if a["session_id"] == row["id"] and a["status"] in {"queued", "running", "waiting"}
            ],
            "pending": [],
        }

    def gateway_url(self, profile, path):
        return self.gateway.base_url.copy_with(path=f"/p/{quote(profile, safe='')}/{path}")

    async def pending(self, row, admissions):
        result = []
        run_ids = {
            admission["run_id"]
            for admission in admissions
            if admission["session_id"] == row["id"]
            and admission["run_id"] is not None
            and admission["status"] in {"queued", "running", "waiting"}
        }
        if row["run_id"] is not None:
            run_ids.add(row["run_id"])
        for run_id in run_ids:
            response = await self.gateway.get(
                self.gateway_url(row["profile"], f"v1/runs/{quote(run_id, safe='')}")
            )
            response.raise_for_status()
            for prompt in response.json().get("pending_controls", []):
                generation = prompt["execution_generation"]
                native_id = prompt["prompt_id"]
                request_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"aide-input:{row['id']}:{run_id}:{generation}:{native_id}",
                    )
                )
                item = {
                    "request_id": request_id,
                    "kind": "approval" if prompt["kind"] == "approval" else "clarification",
                    "prompt": prompt.get("description")
                    or prompt.get("command")
                    or prompt.get("question", ""),
                }
                if item["kind"] == "approval":
                    item["choices"] = [c for c in prompt["choices"] if c in {"once", "deny"}]
                result.append((item, run_id, prompt))
        return result

    async def project_session(self, row, admissions):
        session = self.session(row, admissions)
        session["pending"] = row["pending_inputs"]
        return session

    async def execute_validated(self, request):
        method, params = request["method"], request["params"]
        scope = Scope.model_validate(params["scope"]) if "scope" in params else None
        if method in {"state.list", "state.get"} and params["resource"] in {"tasks", "goals"}:
            coworld = scope.coworld if scope is not None else params.get("scope_id")
            if not coworld:
                return {
                    "error": {
                        "code": -32000,
                        "message": "Select a Coworld for its work board",
                        "data": {"code": "unsupported"},
                    }
                }
            response = await self.seam.get(
                "/snapshot", params={"store": "coworld", "coworld": coworld}
            )
            if not response.is_success:
                return self.refusal(response)
            board = response.json()
            resource = params["resource"]
            items = []
            for row in board["records"][resource]:
                item = {
                    "id": f"{coworld}:{row['id']}",
                    "scope_id": coworld,
                    "title": row["title"],
                    "description": row["description"] or "",
                    "summary": row.get("where_it_stands") or row.get("result") or "",
                }
                if resource == "goals":
                    item.update(end_state=row["end_state"], status=row["status"])
                else:
                    item.update(
                        goal_id=f"{coworld}:G-{row['goal_id']}"
                        if row["goal_id"] is not None
                        else None,
                        session_ids=[row["held_by"]] if row["held_by"] else [],
                        status={
                            "proposed": "proposed",
                            "unclaimed": "pending",
                            "in-progress": "in_progress",
                            "complete": "completed",
                            "abandoned": "abandoned",
                        }[row["status"]],
                        outcome=row["outcome"],
                        waiting_on={"detail": row["waiting_on"]["detail"]}
                        if row["waiting_on"]
                        else None,
                    )
                items.append(item)
            result = {"resource": resource, "epoch": "native:" + coworld, "revision": board["seq"]}
            if method == "state.get":
                matches = [item for item in items if item["id"] == params["id"]]
                if not matches:
                    return {
                        "error": {
                            "code": -32000,
                            "message": "Unknown record",
                            "data": {"code": "not_found"},
                        }
                    }
                return {**result, "item": matches[0]}
            return {**result, "items": items, "next_cursor": None}
        if method == "task.cancel":
            if (
                scope is None
                or not scope.coworld
                or not params["task_id"].startswith(scope.coworld + ":")
            ):
                return {
                    "error": {
                        "code": -32000,
                        "message": "Task scope mismatch",
                        "data": {"code": "forbidden"},
                    }
                }
            selected = await self.execute_validated(
                {
                    "method": "state.get",
                    "params": {
                        "resource": "tasks",
                        "id": params["task_id"],
                        "scope": params["scope"],
                    },
                }
            )
            if "error" in selected:
                return selected
            if selected["item"]["status"] in {"completed", "abandoned"}:
                return {"requested": False}
            response = await self.seam.post(
                "/store/apply",
                json={
                    "actor": "user:aide",
                    "op": "tasks.abandon",
                    "coworld": scope.coworld,
                    "store": "coworld",
                    "args": {
                        "task_id": params["task_id"][len(scope.coworld) + 1 :],
                        "failure": {
                            "what_failed": "Cancelled by user",
                            "next_step": "No further work requested",
                        },
                    },
                },
            )
            return {"requested": True} if response.is_success else self.refusal(response)
        if method == "session.open":
            response = await self.seam.post(
                "/session-open",
                json={
                    "op_id": request["id"],
                    "canonical_request": params,
                    "client": {"id": "aide", "kind": "ide"},
                    "actor": "user:aide",
                    "session_context": {"contract_version": 1, **scope.model_dump()},
                },
            )
            if not response.is_success:
                return self.refusal(response)
            session_id = response.json()["session_id"]
            response = await self.snapshot(scope, session_id)
            if not response.is_success:
                return self.refusal(response)
            body = response.json()
            return {
                "session": await self.project_session(
                    body["records"]["sessions"][0], body["records"]["admissions"]
                )
            }
        if method == "state.list":
            if params["resource"] != "sessions":
                return {
                    "error": {
                        "code": -32000,
                        "message": "Resource is not supported",
                        "data": {"code": "unsupported"},
                    }
                }
            response = await self.snapshot(scope)
            if not response.is_success:
                return self.refusal(response)
            body = response.json()
            rows = body["records"]["sessions"]
            if scope is not None:
                rows = [r for r in rows if self.session(r, [])["scope"] == scope.model_dump()]
            return {
                "resource": "sessions",
                "epoch": "native",
                "revision": body["seq"],
                "items": [
                    await self.project_session(r, body["records"]["admissions"]) for r in rows
                ],
                "next_cursor": None,
            }
        if method not in {"chat.send", "chat.history", "chat.cancel", "state.get", "input.respond"}:
            return {
                "error": {
                    "code": -32000,
                    "message": "Method is not supported",
                    "data": {"code": "unsupported"},
                }
            }
        session_id = params["id"] if method == "state.get" else params["session_id"]
        response = await self.snapshot(scope, session_id)
        if not response.is_success:
            return self.refusal(response)
        snapshot = response.json()
        row = snapshot["records"]["sessions"][0]
        session = await self.project_session(row, snapshot["records"]["admissions"])
        if session["scope"] != scope.model_dump():
            return {
                "error": {
                    "code": -32000,
                    "message": "Session scope mismatch",
                    "data": {"code": "forbidden"},
                }
            }
        if method == "state.get":
            if params["resource"] != "sessions":
                return {
                    "error": {
                        "code": -32000,
                        "message": "Resource is not supported",
                        "data": {"code": "unsupported"},
                    }
                }
            return {
                "resource": "sessions",
                "epoch": "native",
                "revision": snapshot["seq"],
                "item": session,
            }
        if method == "input.respond":
            pending = await self.pending(row, snapshot["records"]["admissions"])
            selected = [
                (item, run_id, prompt)
                for item, run_id, prompt in pending
                if item["request_id"] == params["request_id"]
            ]
            if not selected or selected[0][0]["kind"] != params["response"]["kind"]:
                return {
                    "error": {
                        "code": -32000,
                        "message": "Input is no longer pending",
                        "data": {"code": "conflict"},
                    }
                }
            item, run_id, prompt = selected[0]
            body = {
                "request_id": prompt["prompt_id"],
                "execution_generation": prompt["execution_generation"],
            }
            if item["kind"] == "approval":
                body["choice"] = params["response"]["choice"]
                action = "approval"
            else:
                body["answer"] = params["response"]["answer"]
                action = "clarify"
            response = await self.gateway.post(
                self.gateway_url(row["profile"], f"v1/runs/{quote(run_id, safe='')}/{action}"),
                json=body,
                headers={"Idempotency-Key": request["id"]},
            )
            if response.status_code in {404, 409}:
                return {
                    "error": {
                        "code": -32000,
                        "message": "Input is no longer pending",
                        "data": {"code": "conflict"},
                    }
                }
            if not response.is_success:
                return self.refusal(response)
            if response.json()["status"] != "resolved":
                return {
                    "error": {
                        "code": -32000,
                        "message": "Input is no longer pending",
                        "data": {"code": "conflict"},
                    }
                }
            return {"applied": True}
        if method == "chat.send":
            messages = params["messages"]
            if not supported_messages(messages):
                return {
                    "error": {
                        "code": -32000,
                        "message": (
                            "Supported input is plain developer/system or named page context "
                            "followed by one "
                            "user message, without extra message fields"
                        ),
                        "data": {"code": "unsupported"},
                    }
                }
            if len(messages) > 1:
                models = await self.seam.get("/models")
                if not models.is_success:
                    return self.refusal(models)
                profile = next(
                    profile
                    for profile in models.json()["profiles"]
                    if profile["profile"] == row["profile"]
                )
                if profile["provider"] != "openrouter" or any(
                    fallback["provider"] != "openrouter" for fallback in profile["fallbacks"]
                ):
                    return {
                        "error": {
                            "code": -32000,
                            "message": (
                                "Structured context requires the configured "
                                "OpenRouter Chat Completions transport"
                            ),
                            "data": {"code": "unsupported"},
                        }
                    }
            response = await self.seam.post(
                "/message",
                json={
                    "op_id": request["id"],
                    "canonical_request": params,
                    "session_id": session_id,
                    "message": messages[-1],
                    "messages": messages,
                    "context": {"contract_version": 1, **scope.model_dump()},
                    "actor": "user:aide",
                },
            )
            if not response.is_success:
                return self.refusal(response)
            return {"accepted": True, "session_id": session_id, "request_id": request["id"]}
        if method == "chat.cancel":
            admissions = snapshot["records"]["admissions"]
            target = [
                a
                for a in admissions
                if a["op_id"] == params["request_id"] and a["session_id"] == session_id
            ]
            if not target:
                return {
                    "error": {
                        "code": -32000,
                        "message": "Unknown chat request",
                        "data": {"code": "not_found"},
                    }
                }
            response = await self.seam.post(
                "/message-cancel", json={"op_id": params["request_id"], "actor": "user:aide"}
            )
            return (
                {"requested": target[0]["status"] in {"queued", "running"}}
                if response.is_success
                else self.refusal(response)
            )
        if "cursor" in params and not params["cursor"].isdigit():
            return {"error": {"code": -32602, "message": "Invalid history cursor"}}
        pending = await self.seam.get("/session-pending", params={"session_id": session_id})
        pending.raise_for_status()
        native = []
        if not pending.json()["pending"]:
            offset = 0
            while True:
                response = await self.gateway.get(
                    self.gateway_url(
                        row["profile"], f"api/sessions/{quote(session_id, safe='')}/messages"
                    ),
                    params={"order": "oldest", "limit": 500, "offset": offset},
                )
                if not response.is_success:
                    return self.refusal(response)
                page = response.json()["data"]
                native.extend(page)
                if len(page) < 500:
                    break
                offset += len(page)
        contextual = await self.seam.post(
            "/messages/context", json={"session_id": session_id, "data": native}
        )
        contextual.raise_for_status()
        messages = contextual.json()["data"]
        items = []
        for message in messages:
            if "client_messages" in message:
                items.extend(
                    {
                        "id": f"{message['request_id']}:{index}",
                        "request_id": message["request_id"],
                        "session_id": session_id,
                        "message": original,
                    }
                    for index, original in enumerate(message["client_messages"])
                )
            else:
                items.append(
                    {
                        "id": str(message["id"]),
                        "session_id": session_id,
                        "message": {
                            key: value
                            for key, value in message.items()
                            if key in {"role", "content", "tool_calls", "tool_call_id", "name"}
                        },
                    }
                )
        for admission in snapshot["records"]["admissions"]:
            if admission["run_id"] is None:
                items.extend(
                    {
                        "id": f"{admission['op_id']}:{i}",
                        "request_id": admission["op_id"],
                        "session_id": session_id,
                        "message": message,
                    }
                    for i, message in enumerate(admission["messages"])
                )
        for i, item in enumerate(items):
            item["sequence"] = i + 1
        end = int(params["cursor"]) if "cursor" in params else len(items)
        if end > len(items):
            return {
                "error": {
                    "code": -32000,
                    "message": "History cursor expired",
                    "data": {"code": "snapshot_expired"},
                }
            }
        limit = params.get("limit", 50)
        start = end
        selected = []
        for index in range(end - 1, max(-1, end - limit - 1), -1):
            candidate = {
                "items": [items[index], *selected],
                "next_cursor": str(index) if index else None,
            }
            if (
                self.size({"jsonrpc": "2.0", "id": request["id"], "result": candidate})
                > self.max_message_bytes
            ):
                break
            selected.insert(0, items[index])
            start = index
        if not selected and end:
            return {
                "error": {
                    "code": -32000,
                    "message": "One history record exceeds the message limit",
                    "data": {"code": "too_large"},
                }
            }
        return {"items": selected, "next_cursor": str(start) if start else None}

    async def notify(self, socket, frontend_id, method, params):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        self.validate("Notification", message)
        if self.size(message) > self.max_message_bytes:
            log.warning("AIDE notification exceeds message limit: %s", method)
            return
        await socket.send(
            json.dumps(
                {"frontend_id": frontend_id, "message": message},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    async def observe(self, socket, frontend_id, request):
        params = request["params"]
        scope = Scope.model_validate(params["scope"])
        previous_revision = None
        delivered = {}
        while True:
            response = await self.snapshot(scope, params["session_id"])
            response.raise_for_status()
            snapshot = response.json()
            admissions = snapshot["records"]["admissions"]
            selected = [a for a in admissions if a["op_id"] == request["id"]]
            current = await self.project_session(snapshot["records"]["sessions"][0], admissions)
            revision = snapshot["seq"]
            if revision != previous_revision:
                await self.notify(
                    socket,
                    frontend_id,
                    "state.changed",
                    {
                        "resource": "sessions",
                        "epoch": "native",
                        "previous_revision": previous_revision
                        if previous_revision is not None
                        else max(0, revision - 1),
                        "revision": revision,
                        "change": "upsert",
                        "item": current,
                    },
                )
                previous_revision = revision
            cursor = None
            while True:
                history = await self.execute(
                    {
                        "jsonrpc": "2.0",
                        "id": str(uuid4()),
                        "method": "chat.history",
                        "params": {
                            "session_id": params["session_id"],
                            "scope": scope.model_dump(),
                            "limit": 100,
                            **({"cursor": cursor} if cursor is not None else {}),
                        },
                    }
                )
                if "error" in history:
                    break
                for item in history["items"]:
                    if delivered.get(item["id"]) != item:
                        await self.notify(socket, frontend_id, "chat.message", item)
                        delivered[item["id"]] = item
                cursor = history["next_cursor"]
                if cursor is None:
                    break
            if selected and selected[0]["status"] in {
                "completed",
                "cancelled",
                "failed",
                "interrupted",
            }:
                status = selected[0]["status"]
                finished = {
                    "session_id": params["session_id"],
                    "request_id": request["id"],
                    "status": status if status in {"completed", "cancelled"} else "failed",
                }
                if finished["status"] == "failed":
                    finished["error"] = {
                        "code": "execution_failed",
                        "message": selected[0]["error"] or status,
                    }
                await self.notify(socket, frontend_id, "chat.finished", finished)
                return
            await asyncio.sleep(2)

    @staticmethod
    def refusal(response):
        code = {404: "not_found", 409: "forbidden", 400: "invalid", 422: "invalid"}.get(
            response.status_code, "unavailable"
        )
        if (
            response.status_code == 400
            and "op_id was already used for different input" in response.text
        ):
            code = "id_reused"
        if code == "invalid":
            return {"error": {"code": -32602, "message": response.text}}
        return {"error": {"code": -32000, "message": response.text, "data": {"code": code}}}


async def serve(*, platform_url, workspace_id, credential, env, connected, inference):
    url = platform_url.rstrip("/") + f"/v2/workspaces/{workspace_id}/aide/1.0.0/backend"
    url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650", timeout=30) as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642/p/orchestrator/",
            headers={"Authorization": f"Bearer {env['API_SERVER_KEY']}"},
            timeout=30,
        ) as gateway,
    ):
        app = Application(seam, gateway)

        async def session():
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {credential}"},
                subprotocols=["aide.1.0.0"],
                max_size=1048576 + 4096,
            ) as socket:
                binding = json.loads(await socket.recv())
                assert binding["binding"]["version"] == "1.0.0"
                connected.set()
                watching = set()
                running = set()
                async with asyncio.TaskGroup() as operations:

                    async def guarded(operation):
                        (result,) = await asyncio.gather(operation, return_exceptions=True)
                        if isinstance(result, ConnectionClosed):
                            return
                        if isinstance(result, BaseException):
                            raise result

                    def spawn(operation):
                        task = operations.create_task(guarded(operation))
                        running.add(task)
                        task.add_done_callback(running.discard)
                        return task

                    async def answer(frame):
                        message = frame["message"]
                        if message["method"] == "chat.send" and not inference.available():
                            receipt = await seam.post(
                                "/aide-command",
                                json={
                                    "request_id": message["id"],
                                    "method": message["method"],
                                    "params": message["params"],
                                },
                            )
                            receipt.raise_for_status()
                            known = receipt.json()
                            if known["state"] == "recorded":
                                result = known["result"]
                            else:
                                result = {
                                    "error": {
                                        "code": -32000,
                                        "message": "Inference lease unavailable"
                                        if known["state"] != "id_reused"
                                        else "Request identity was reused",
                                        "data": {
                                            "code": "unavailable"
                                            if known["state"] != "id_reused"
                                            else "id_reused"
                                        },
                                    }
                                }
                        else:
                            (result,) = await asyncio.gather(
                                app.execute(message), return_exceptions=True
                            )
                        if isinstance(result, httpx.TransportError):
                            result = {
                                "error": {
                                    "code": -32000,
                                    "message": "Native service transport unavailable",
                                    "data": {"code": "unavailable", "outcome": "unknown"},
                                }
                            }
                        if isinstance(result, BaseException):
                            raise result
                        reply = {"jsonrpc": "2.0", "id": message["id"]}
                        reply.update(result if "error" in result else {"result": result})
                        await socket.send(
                            json.dumps(
                                {"frontend_id": frame["frontend_id"], "message": reply},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        )
                        key = (frame["frontend_id"], message["id"])
                        if (
                            message["method"] == "chat.send"
                            and "error" not in result
                            and key not in watching
                        ):
                            watching.add(key)
                            task = spawn(app.observe(socket, frame["frontend_id"], message))
                            task.add_done_callback(lambda _, key=key: watching.discard(key))

                    async def receive():
                        async for raw in socket:
                            frame = json.loads(raw)
                            message = frame["message"]
                            if "id" in message and "method" in message:
                                spawn(answer(frame))

                    (closed,) = await asyncio.gather(receive(), return_exceptions=True)
                    for task in running:
                        task.cancel()
                if isinstance(closed, BaseException):
                    raise closed

        while True:
            (result,) = await asyncio.gather(session(), return_exceptions=True)
            temporary = isinstance(result, (ConnectionClosed, OSError, httpx.TransportError)) or (
                isinstance(result, InvalidStatus) and result.response.status_code in {502, 503, 504}
            )
            if isinstance(result, BaseException) and not temporary:
                raise result
            await asyncio.sleep(1)

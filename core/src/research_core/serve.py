"""``research serve``: the local seam server (design §10, A.4, A.11; plan entry 6; A6.1).

Locally this is the bridge: a small stdlib HTTP server that gives a client the seam without
metta. It opens chat Sessions and durably queues each message for a Hermes run,
stamps the client and the active Coworld into the context block, streams each store's
``events`` rows as Server-Sent Events, serves snapshots, passes UI actions into the Session and
runs the user-edit allow-list. It is the reference for the bridge's per-session rule (A6.1):
one live run per Session; messages arriving during a run wait in the workspace inbox.

What the server stamps into a turn is only ``client``, ``coworld`` and ``contract_version``:
the profile's own turn hook (``plugin/turn.py``) adds the unread notices and the changed-since
note, so the server never duplicates them. ``ThreadingHTTPServer`` gives
each request its own thread, and each request opens its own store connections (SQLite
connections are per thread). A background dispatcher reconciles each persisted input using
Hermes's durable idempotency key. Its filesystem lock also fences the separate sleep command.

Two conveniences sit beside the contract's routes: ``GET /`` (and ``/ui``) serves the client
page ``ui/index.html``, a self-contained page that exercises every route from a browser, and
``GET /status`` answers what ``research watcher status`` prints, read from the workspace store,
so the page can show the watcher's liveness without a shell.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from research_core import contract, inference, layout
from research_core.store import admissions, coworlds, sessions, settings, ui_state
from research_core.store import cli as store_cli
from research_core.store._common import parse_iso
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError
from research_core.tools.ui import WORKSPACE_CONVERSATION, pointer_token
from research_core.watcher import hermes_api
from research_core.watcher.heartbeat import HAS_WORK_KEY, HAS_WORK_REASONS_KEY, HEARTBEAT_KEY
from research_core.watcher.hermes_api import HermesRuns, RunsBusy, RunsError
from research_core.watcher.tick import PAUSED_DRAINED_KEY

log = logging.getLogger(__name__)

LIVE_STATUSES = frozenset({"running", "queued"})
TAIL_INTERVAL_S = 0.5  # how often the stream looks for new events rows (A.11)
KEEPALIVE_S = 10.0  # an SSE comment keeps an idle connection open through proxies
STREAM_BATCH = 500
DEFAULT_PORT = 8650
DEFAULT_ACTOR = "user:local"
RETRY_AFTER_S = 1
UI_ACTIONS = ("answer", "reaction")
PAGE_PATHS = ("/", "/ui")
PAGE_FILE = resources.files("research_core").joinpath("ui", "index.html")


class Refused(Exception):
    """A request the server answers with an error status and a JSON body, never a traceback."""

    def __init__(
        self,
        status: int,
        error: str,
        *,
        problems: list[str] | None = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(error)
        self.status = status
        self.headers = headers or {}
        self.body = {"ok": False, "error": error, "problems": list(problems or []), **extra}


def _gateway_error(error: RunsError) -> Refused:
    """A runs API failure as a reply: 503 with ``Retry-After`` when the gateway is busy or
    unreachable (the client retries), 502 otherwise. The gateway's own HTTP status is kept
    as ``gateway_status`` beside the reply's."""
    body = error.to_dict()
    detail = body.pop("error")
    body["gateway_status"] = body.pop("status")
    if isinstance(error, RunsBusy):
        return Refused(
            HTTPStatus.SERVICE_UNAVAILABLE,
            detail,
            headers={"Retry-After": str(RETRY_AFTER_S)},
            retry_after=RETRY_AFTER_S,
            **body,
        )
    return Refused(HTTPStatus.BAD_GATEWAY, detail, **body)


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refused(HTTPStatus.BAD_REQUEST, f"{key} is required: a non-empty string")
    return value


def _object(body: dict, key: str, default: dict | None = None) -> dict:
    value = body.get(key, default)
    if not isinstance(value, dict):
        raise Refused(HTTPStatus.BAD_REQUEST, f"{key} must be a JSON object")
    return value


def _actor(text: str | None) -> Actor:
    """``user:<id>`` or ``harness`` (``user:local`` when absent); a session actor is refused."""
    try:
        return store_cli.parse_actor(text or DEFAULT_ACTOR)
    except store_cli.Refusal as error:
        raise Refused(HTTPStatus.BAD_REQUEST, error.error, problems=error.problems) from None


class SeamServer:
    """The seam over one workspace and one Hermes gateway. See the module docstring."""

    def __init__(
        self,
        *,
        ws: layout.Workspace,
        hermes: HermesRuns,
        profile: str = "orchestrator",
        host: str = "127.0.0.1",
        port: int = 0,
        inference_lease_file: Path | None = None,
    ) -> None:
        self.ws = ws
        self.hermes = hermes
        self.profile = profile
        self.inference_lease_file = inference_lease_file
        # The client that opened each Session, for the stamp (A.4). A Session this instance
        # did not open is stamped with the client the context block names, if any.
        self._clients: dict[str, dict[str, Any]] = {}
        self._deliver_lock = threading.Lock()
        self.stopping = threading.Event()
        # Initialize the schema before the dispatcher and HTTP handlers can open it concurrently.
        Store.open(self.ws.store_path, kind="workspace").close()
        self._http = _HttpServer((host, port), self)

    @property
    def url(self) -> str:
        host, port = self._http.server_address[:2]
        return f"http://{host}:{port}"

    def serve_forever(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as worker:
            dispatch = worker.submit(asyncio.run, self.dispatch_forever())
            dispatch.add_done_callback(lambda _: self._http.shutdown())
            try:
                self._http.serve_forever(poll_interval=0.2)
            finally:
                self.stopping.set()
            dispatch.result()

    async def dispatch_forever(self) -> None:
        while not self.stopping.is_set():
            (result,) = await asyncio.gather(self.dispatch_messages(), return_exceptions=True)
            if isinstance(result, RunsError):
                log.warning("Chat dispatch unavailable: %s", result)
            elif isinstance(result, BaseException):
                raise result
            await asyncio.sleep(0.5)

    def shutdown(self) -> None:
        """Stop accepting requests and end every open stream; safe to call more than once."""
        self.stopping.set()
        self._http.shutdown()
        self._http.server_close()

    # --- stores ---------------------------------------------------------------------------

    @contextmanager
    def stores(self) -> Iterator[store_cli.Stores]:
        """The workspace store and, on first use, the active Coworld's; closed on exit."""
        wstore = Store.open(self.ws.store_path, kind="workspace")
        active = coworlds.active(wstore)
        with store_cli.Stores(self.ws, wstore, active["coworld_id"] if active else None) as s:
            yield s

    @staticmethod
    def store_for(stores: store_cli.Stores, kind: str) -> Store:
        if kind not in store_cli.STORE_KINDS:
            raise Refused(
                HTTPStatus.BAD_REQUEST, f"store must be one of {', '.join(store_cli.STORE_KINDS)}"
            )
        try:
            return stores.by_kind(kind)
        except StoreError as error:
            raise Refused(HTTPStatus.NOT_FOUND, error.problems[0]) from None

    # --- the routes -----------------------------------------------------------------------

    def health(self) -> dict:
        with self.stores() as stores:
            return {
                "ok": True,
                "profile": self.profile,
                "coworld": stores.coworld_id,
                "contract_version": contract.CONTRACT_VERSION,
            }

    def status(self) -> dict:
        """What ``research watcher status`` prints (``watcher/cli.py``): the last heartbeat and
        its age, the has-work signal with its reasons, and the pause state, from the workspace
        store's meta and the ``paused`` setting. Nothing else is read."""
        with self.stores() as stores:
            wstore = stores.wstore
            heartbeat = wstore.get_meta(HEARTBEAT_KEY)
            age_s = None
            if heartbeat is not None:
                age_s = round((datetime.now(UTC) - parse_iso(heartbeat)).total_seconds(), 1)
            return {
                "ok": True,
                "heartbeat": heartbeat,
                "heartbeat_age_s": age_s,
                "has_work": wstore.get_meta(HAS_WORK_KEY) == "1"
                or bool(
                    wstore.one(
                        "SELECT 1 FROM client_admissions"
                        " WHERE status IN ('queued','running') LIMIT 1"
                    )
                ),
                "waiting_on": json.loads(wstore.get_meta(HAS_WORK_REASONS_KEY) or "[]"),
                "paused": bool(settings.get(wstore, "paused")),
                "paused_drained": wstore.get_meta(PAUSED_DRAINED_KEY) == "1",
                "sleep_started": wstore.get_meta("sleep_started"),
            }

    @staticmethod
    def page() -> bytes:
        """The client page, read from the package on each request so edits show on reload."""
        return PAGE_FILE.read_bytes()

    def session_open(self, body: dict) -> dict:
        """Mint a session id and register a chat Session with the client's context (A.2)."""
        client = _object(body, "client")
        kept, problems = contract.validate_context(
            {"contract_version": contract.CONTRACT_VERSION, "client": client}
        )
        client = kept.get("client") or {}
        if not client.get("id") or not client.get("kind"):
            raise Refused(
                HTTPStatus.BAD_REQUEST, "client is required: {id, kind, guide?}", problems=problems
            )
        actor = _actor(body.get("actor"))
        op_id = str(UUID(_text(body, "op_id")))
        with self._deliver_lock, self.stores() as stores:
            receipt = admissions.read(stores.wstore, op_id, "open", body)
            session_id = receipt["session_id"] if receipt else hermes_api.mint_session_id()
            if receipt is None:
                admissions.accept(
                    stores.wstore,
                    actor,
                    op_id=op_id,
                    kind="open",
                    request=body,
                    session_id=session_id,
                    message="",
                    payload="",
                )
            try:
                sessions.register(
                    stores.wstore,
                    actor,
                    session_id=session_id,
                    profile=self.profile,
                    kind="chat",
                    coworld_id=stores.coworld_id,
                    session_context=body.get("session_context"),
                    client_capabilities=body.get("capabilities"),
                )
            except StoreError as error:
                raise Refused(
                    HTTPStatus.BAD_REQUEST, "session-open refused", problems=error.problems
                ) from None
            self._clients[session_id] = client
            return {
                "session_id": session_id,
                "client": client,
                "coworld": stores.coworld_id,
                "contract_version": contract.CONTRACT_VERSION,
                "problems": problems,
            }

    def message(self, body: dict) -> dict:
        """Confirm a durable input once; dispatch serially with Hermes's durable run key."""
        session_id = _text(body, "session_id")
        text = _text(body, "message")
        actor = _actor(body.get("actor"))
        op_id = str(UUID(_text(body, "op_id")))
        with self._deliver_lock, self.stores() as stores:
            self._require_session(stores, session_id)
            receipt = admissions.read(stores.wstore, op_id, "message", body)
            if receipt is not None:
                return {
                    "op_id": op_id,
                    "session_id": session_id,
                    "run_id": receipt["run_id"],
                    "delivered": "queued",
                    "problems": [],
                    "seq": stores.wstore.last_seq(),
                }
            block, problems = self._context_block(stores, session_id, body.get("context"))
            payload = text
            if block is not None:
                payload = f"{text}\n\n{contract.BLOCK_LABEL}\n{json.dumps(block, sort_keys=True)}"
            admissions.accept(
                stores.wstore,
                actor,
                op_id=op_id,
                kind="message",
                request=body,
                session_id=session_id,
                message=text,
                payload=payload,
            )
            return {
                "op_id": op_id,
                "session_id": session_id,
                "run_id": None,
                "delivered": "queued",
                "problems": problems,
                "seq": stores.wstore.last_seq(),
            }

    async def dispatch_messages(self) -> None:
        with self._deliver_lock, self.stores() as stores:
            actor = Actor.user("client-dispatch")
            reserved: set[str] = set()
            for row in stores.wstore.q(
                "SELECT * FROM client_admissions WHERE kind='message'"
                " AND status IN ('queued','running') ORDER BY rowid"
            ):
                if row["session_id"] in reserved:
                    continue
                reserved.add(row["session_id"])
                with admissions.dispatch_lock(stores.wstore):
                    if row["run_id"]:
                        session = self._require_session(stores, row["session_id"])
                        if session["run_id"] != row["run_id"]:
                            sessions.set_run(stores.wstore, actor, row["session_id"], row["run_id"])
                        if row["cancel_requested"]:
                            self.hermes.stop(row["run_id"], profile=self.profile)
                        status = self.hermes.status(row["run_id"], profile=self.profile)
                        if status.terminal:
                            terminal = (
                                status.status if status.status != "unknown" else "interrupted"
                            )
                            with stores.wstore.tx(actor) as tx:
                                tx.execute(
                                    "UPDATE client_admissions SET status=?,error=? WHERE op_id=?",
                                    (terminal, status.error, row["op_id"]),
                                )
                                tx.event("client.finished", row["op_id"], {"status": terminal})
                        continue
                    if row["cancel_requested"] and not row["dispatch_started"]:
                        with stores.wstore.tx(actor) as tx:
                            tx.execute(
                                "UPDATE client_admissions SET status='cancelled' WHERE op_id=?",
                                (row["op_id"],),
                            )
                            tx.event("client.cancelled", row["op_id"], {})
                        continue
                    if settings.get(stores.wstore, "paused") and not row["dispatch_started"]:
                        continue
                    if not inference.available(self.inference_lease_file):
                        continue
                    session = self._require_session(stores, row["session_id"])
                    if (
                        not row["dispatch_started"]
                        and session["run_id"]
                        and not self.hermes.status(session["run_id"], profile=self.profile).terminal
                    ):
                        continue
                    with stores.wstore.tx(actor) as tx:
                        tx.execute(
                            "UPDATE client_admissions SET dispatch_started=1 WHERE op_id=?",
                            (row["op_id"],),
                        )
                    (run_id,) = await asyncio.gather(
                        asyncio.to_thread(
                            self.hermes.start,
                            profile=self.profile,
                            session_id=row["session_id"],
                            text=row["payload"],
                            idempotency_key=row["op_id"],
                        ),
                        return_exceptions=True,
                    )
                    if isinstance(run_id, RunsError):
                        with stores.wstore.tx(actor) as tx:
                            if run_id.status == 429 or run_id.code in {
                                "gateway_draining",
                                "inference_unavailable",
                            }:
                                tx.execute(
                                    "UPDATE client_admissions SET dispatch_started=0 WHERE op_id=?",
                                    (row["op_id"],),
                                )
                            tx.execute(
                                "UPDATE client_admissions SET error=?,status=? WHERE op_id=?",
                                (
                                    run_id.detail,
                                    "queued" if run_id.retryable else "failed",
                                    row["op_id"],
                                ),
                            )
                            tx.event(
                                "client.delivery_failed",
                                row["op_id"],
                                {"retryable": run_id.retryable},
                            )
                        continue
                    if isinstance(run_id, BaseException):
                        raise run_id
                    with stores.wstore.tx(actor) as tx:
                        tx.execute(
                            "UPDATE client_admissions SET run_id=?,status='running',error=NULL"
                            " WHERE op_id=?",
                            (run_id, row["op_id"]),
                        )
                        tx.event("client.started", row["op_id"], {"run_id": run_id})
                    sessions.set_run(stores.wstore, actor, row["session_id"], run_id)
                    if row["cancel_requested"]:
                        self.hermes.stop(run_id, profile=self.profile)

    def message_cancel(self, body: dict) -> dict:
        op_id = str(UUID(_text(body, "op_id")))
        with self._deliver_lock, self.stores() as stores:
            row = stores.wstore.one(
                "SELECT * FROM client_admissions WHERE op_id=? AND kind='message'", (op_id,)
            )
            if row is None:
                raise Refused(HTTPStatus.NOT_FOUND, "Unknown message admission")
            if row["status"] in {"queued", "running"}:
                with stores.wstore.tx(_actor(body.get("actor"))) as tx:
                    tx.execute(
                        "UPDATE client_admissions SET cancel_requested=1 WHERE op_id=?", (op_id,)
                    )
                    if not row["dispatch_started"]:
                        tx.execute(
                            "UPDATE client_admissions SET status='cancelled' WHERE op_id=?",
                            (op_id,),
                        )
                    tx.event("client.cancel_requested", op_id, {})
            if row["run_id"]:
                self.hermes.stop(row["run_id"], profile=self.profile)
            return {"op_id": op_id, "stop_requested": True}

    def ui_action(self, body: dict) -> dict:
        """An answer goes into the Session at once; a reaction is feedback and starts no run."""
        session_id = _text(body, "session_id")
        action = _text(body, "action")
        payload = _object(body, "payload", {})
        actor = _actor(body.get("actor"))
        if action not in UI_ACTIONS:
            raise Refused(
                HTTPStatus.BAD_REQUEST, f"unknown action {action!r}; one of {', '.join(UI_ACTIONS)}"
            )
        with self.stores() as stores:
            self._require_session(stores, session_id)
            if action == "answer":
                return self._answer(stores, actor, session_id, payload)
            return self._reaction(stores, actor, session_id, payload)

    def store_apply(self, body: dict) -> dict:
        """One allowed library function as a user or harness actor (A.10, A2.10)."""
        op = _text(body, "op")
        actor = _actor(_text(body, "actor"))
        args = _object(body, "args", {})
        try:
            if op not in store_cli.ALLOWED:
                allowed = ", ".join(sorted(store_cli.ALLOWED))
                raise store_cli.Refusal(f"{op!r} is not an op store apply runs; allowed: {allowed}")
            op_args = store_cli.parse_op_args(json.dumps(args))
            with self.stores() as stores:
                kind = body.get("store") or store_cli.store_kind_for(op, op_args, stores)
                if kind not in store_cli.STORE_KINDS:
                    raise store_cli.Refusal(
                        f"store must be one of {', '.join(store_cli.STORE_KINDS)}"
                    )
                record = store_cli._call(op, kind, stores, actor, op_args)
                seq = stores.by_kind(kind).last_seq()
        except store_cli.Refusal as error:
            raise Refused(HTTPStatus.BAD_REQUEST, error.error, problems=error.problems) from None
        except StoreError as error:
            raise Refused(
                HTTPStatus.BAD_REQUEST, f"{op} refused", problems=error.problems
            ) from None
        return {"ok": True, "op": op, "store": kind, "record": record, "seq": seq}

    def snapshot(self, kind: str) -> dict:
        with self.stores() as stores:
            return store_cli.snapshot(self.store_for(stores, kind), stores.coworld_id)

    def run_status(self, run_id: str) -> dict:
        return dataclasses.asdict(self.hermes.status(run_id, profile=self.profile))

    # --- the pieces -----------------------------------------------------------------------

    def _require_session(self, stores: store_cli.Stores, session_id: str) -> dict:
        session = sessions.read(stores.wstore, session_id)
        if session is None:
            raise Refused(
                HTTPStatus.NOT_FOUND, f"unknown session {session_id}; call /session-open first"
            )
        return session

    def _context_block(
        self, stores: store_cli.Stores, session_id: str, context: object
    ) -> tuple[dict | None, list[str]]:
        """The validated block with the server's stamps, or ``None`` when there is nothing to
        send: no context and no client known for the Session."""
        client = self._clients.get(session_id)
        if client is None:
            opened = stores.wstore.one(
                "SELECT request FROM client_admissions WHERE session_id=? AND kind='open'",
                (session_id,),
            )
            if opened is not None:
                client = json.loads(opened["request"])["client"]
        if context is None and client is None:
            return None, []
        kept, problems = ({}, []) if context is None else contract.validate_context(context)
        block = {**kept, "contract_version": contract.CONTRACT_VERSION}
        if client is not None:
            block["client"] = client
        if stores.coworld_id is not None:
            shown = kept.get("coworld")
            if shown and shown != stores.coworld_id:
                problems.append(
                    f"context.coworld: the client shows {shown}; the active Coworld is"
                    f" {stores.coworld_id} (stamped)"
                )
            block["coworld"] = stores.coworld_id
        return block, problems

    def _deliver(self, stores: store_cli.Stores, actor: Actor, session_id: str, text: str) -> dict:
        """A6.1: steer the Session's live run, else start one with the same session id."""
        with self._deliver_lock:
            run_id = self._require_session(stores, session_id).get("run_id")
            try:
                if run_id and self._is_live(run_id):
                    if self.hermes.steer(run_id, text, profile=self.profile):
                        return {"session_id": session_id, "run_id": run_id, "delivered": "steer"}
                    if self._is_live(run_id):  # queued: not steerable yet, and a second run
                        raise Refused(  # would race it, so the client retries shortly
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            f"run {run_id} is queued and cannot take input yet; retry",
                            headers={"Retry-After": str(RETRY_AFTER_S)},
                            retry_after=RETRY_AFTER_S,
                            retryable=True,
                        )
                run_id = self.hermes.start(profile=self.profile, session_id=session_id, text=text)
            except RunsError as error:
                raise _gateway_error(error) from None
            sessions.set_run(stores.wstore, actor, session_id, run_id)
            return {"session_id": session_id, "run_id": run_id, "delivered": "start"}

    def _is_live(self, run_id: str) -> bool:
        return self.hermes.status(run_id, profile=self.profile).status in LIVE_STATUSES

    def _answer(
        self, stores: store_cli.Stores, actor: Actor, session_id: str, payload: dict
    ) -> dict:
        ask_id = _text(payload, "ask_id")
        if "value" not in payload:
            raise Refused(HTTPStatus.BAD_REQUEST, "value is required: the user's answer")
        value = payload["value"]
        conversation = self._ask_conversation(stores.wstore, session_id, ask_id)
        ask = ui_state.get(stores.wstore, conversation)["doc"]["asks"][ask_id]
        if ask.get("status") != "open":
            raise Refused(
                HTTPStatus.CONFLICT,
                f"{ask_id} was already answered; the first answer wins",
                answer=ask.get("answer"),
                answered_by=ask.get("answered_by"),
            )
        path = f"/asks/{pointer_token(ask_id)}"
        record = ui_state.patch(
            stores.wstore,
            actor,
            conversation,
            [
                {"op": "test", "path": f"{path}/status", "value": "open"},
                {"op": "replace", "path": f"{path}/status", "value": "answered"},
                {"op": "add", "path": f"{path}/answer", "value": value},
                {"op": "add", "path": f"{path}/answered_by", "value": str(actor)},
            ],
        )
        shown = value if isinstance(value, str) else json.dumps(value)
        result = self._deliver(
            stores, actor, session_id, f"[UI action] answer to {ask_id}: {shown}"
        )
        return {
            "accepted": True,
            "conversation": conversation,
            "version": record["version"],
            **result,
        }

    @staticmethod
    def _ask_conversation(wstore: Store, session_id: str, ask_id: str) -> str:
        """Where the ask lives (A3.5): the chat's own conversation, else ``workspace``."""
        for conversation in (session_id, WORKSPACE_CONVERSATION):
            if ask_id in ui_state.get(wstore, conversation)["doc"].get("asks", {}):
                return conversation
        raise Refused(
            HTTPStatus.NOT_FOUND, f"{ask_id} is not an ask of this session or the workspace"
        )

    def _reaction(
        self, stores: store_cli.Stores, actor: Actor, session_id: str, payload: dict
    ) -> dict:
        """Feedback the agent sees at its next turn, as a UI state change under ``/reactions``.
        It never starts a run (design A.5)."""
        message_id = _text(payload, "message_id")
        if "value" not in payload:
            raise Refused(HTTPStatus.BAD_REQUEST, "value is required: the reaction")
        wstore = stores.wstore
        ops: list[dict[str, Any]] = []
        if "reactions" not in ui_state.get(wstore, session_id)["doc"]:
            ops.append({"op": "add", "path": "/reactions", "value": {}})
        ops.append(
            {
                "op": "add",
                "path": f"/reactions/{pointer_token(message_id)}",
                "value": {"value": payload["value"], "by": str(actor), "at": wstore.now()},
            }
        )
        record = ui_state.patch(wstore, actor, session_id, ops)
        return {
            "accepted": True,
            "conversation": session_id,
            "version": record["version"],
            "delivered": "none",
        }


# --- HTTP -------------------------------------------------------------------------------------


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], seam: SeamServer) -> None:
        super().__init__(address, _Handler)
        self.seam = seam


class _Handler(BaseHTTPRequestHandler):
    server: _HttpServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base signature
        log.debug("%s - %s", self.address_string(), format % args)

    # -- routing --------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        url = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        seam = self.server.seam
        try:
            if url.path in PAGE_PATHS:
                self._html(HTTPStatus.OK, seam.page())
            elif url.path == "/health":
                self._json(HTTPStatus.OK, seam.health())
            elif url.path == "/status":
                self._json(HTTPStatus.OK, seam.status())
            elif url.path == "/snapshot":
                self._json(HTTPStatus.OK, seam.snapshot(query.get("store", "workspace")))
            elif url.path == "/stream":
                self._stream(query)
            elif url.path.startswith("/runs/") and len(url.path) > len("/runs/"):
                self._json(HTTPStatus.OK, seam.run_status(url.path[len("/runs/") :]))
            else:
                raise Refused(HTTPStatus.NOT_FOUND, f"no route {url.path}")
        except Exception as error:  # noqa: BLE001 - every failure becomes a JSON error
            self._error(error)

    def do_POST(self) -> None:  # noqa: N802 - http.server's naming
        path = urlsplit(self.path).path
        seam = self.server.seam
        routes = {
            "/session-open": seam.session_open,
            "/message": seam.message,
            "/message-cancel": seam.message_cancel,
            "/ui-action": seam.ui_action,
            "/store/apply": seam.store_apply,
        }
        try:
            handler = routes.get(path)
            if handler is None:
                raise Refused(HTTPStatus.NOT_FOUND, f"no route POST {path}")
            self._json(HTTPStatus.OK, handler(self._body()))
        except Exception as error:  # noqa: BLE001 - every failure becomes a JSON error
            self._error(error)

    # -- bodies and replies -----------------------------------------------------------------

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError as error:
            raise Refused(HTTPStatus.BAD_REQUEST, f"the body is not JSON: {error}") from None
        if not isinstance(body, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, "the body must be a JSON object")
        return body

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _html(self, status: int, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, error: Exception) -> None:
        if isinstance(error, RunsError):
            error = _gateway_error(error)
        if isinstance(error, Refused):
            self._json(error.status, error.body, error.headers)
        elif isinstance(error, StoreError):
            body = {"ok": False, "error": str(error), "problems": list(error.problems)}
            self._json(HTTPStatus.BAD_REQUEST, body)
        else:
            log.exception("request failed: %s %s", self.command, self.path)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": f"{type(error).__name__}: {error}",
                    "problems": [],
                    "traceback": traceback.format_exc()[-2000:],
                },
            )

    # -- the event stream -------------------------------------------------------------------

    def _stream(self, query: dict[str, str]) -> None:
        """``text/event-stream`` of one store's events after a seq (A.11).

        Frames are ``id: <seq>`` and ``data: <event JSON>``; a ``: keepalive`` comment goes out
        after ten idle seconds. A reconnecting client's ``Last-Event-ID`` header wins over
        ``after``. The loop ends when the client goes away or the server stops.
        """
        seam = self.server.seam
        kind = query.get("store", "workspace")
        after_text = self.headers.get("Last-Event-ID") or query.get("after", "0")
        try:
            cursor = int(after_text)
        except ValueError:
            raise Refused(
                HTTPStatus.BAD_REQUEST, f"after must be an integer seq, not {after_text!r}"
            ) from None
        with seam.stores() as stores:
            store = seam.store_for(stores, kind)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self._write(f": stream {kind} after {cursor}\n\n")
                last_write = time.monotonic()
                while not seam.stopping.is_set():
                    events = store.events_after(cursor, limit=STREAM_BATCH)
                    for event in events:
                        data = json.dumps({"store": kind, **event.to_dict()}, ensure_ascii=False)
                        self._write(f"id: {event.seq}\ndata: {data}\n\n")
                        cursor = event.seq
                    if events:
                        last_write = time.monotonic()
                        continue
                    if time.monotonic() - last_write >= KEEPALIVE_S:
                        self._write(": keepalive\n\n")
                        last_write = time.monotonic()
                    seam.stopping.wait(TAIL_INTERVAL_S)
            except OSError:
                return  # the client closed the connection

    def _write(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


# --- the command ------------------------------------------------------------------------------


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT}; 0 picks one)"
    )
    parser.add_argument(
        "--profile", default="orchestrator", help="The Hermes profile that runs the chat Sessions"
    )


def run(args: argparse.Namespace) -> int:
    """Serve the workspace of ``IDE_WORKSPACE`` against the gateway of ``HERMES_HOME/.env``."""
    env = os.environ
    profile_home = Path(env.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    researcher_profiles = [
        name.strip() for name in env.get("IDE_RESEARCHER_PROFILES", "researcher").split(",")
    ]
    hermes = HermesRuns.from_profile(
        profile_home, researcher_profiles=[name for name in researcher_profiles if name]
    )
    server = SeamServer(
        ws=layout.from_env(env),
        hermes=hermes,
        profile=args.profile,
        host=args.host,
        port=args.port,
        inference_lease_file=Path(env["IDE_INFERENCE_LEASE_FILE"])
        if "IDE_INFERENCE_LEASE_FILE" in env
        else None,
    )
    print(f"research serve: {server.url} (profile {args.profile}, Hermes {hermes.base_url})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research serve", description=run.__doc__)
    _add_arguments(parser)
    return run(parser.parse_args(argv))


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "serve",
        help="Serve the client seam locally: sessions, messages, UI actions, the event stream",
        description=(
            "The local stand-in for the bridge (design §10, A.11): POST /session-open, /message,"
            " /ui-action and /store/apply; GET /snapshot, /stream, /runs/<id> and /health;"
            " plus GET / (the client page) and GET /status (what watcher status prints)."
        ),
    )
    _add_arguments(parser)
    parser.set_defaults(run=run)

"""Check canonical application calls against packaged research serve and native gateway."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, "/opt/distribution/image")
from aide import Application


async def main():
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650", timeout=30) as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642/p/orchestrator/",
            headers={
                "Authorization": "Bearer "
                + Path("/workspace/host/.gateway-key").read_text().strip()
            },
            timeout=30,
        ) as gateway,
    ):
        app = Application(seam, gateway)
        scope = {"scope": "workspace", "coworld": None, "league": None}

        async def call(method, params, request_id=None):
            return await app.execute(
                {
                    "jsonrpc": "2.0",
                    "id": request_id or str(uuid4()),
                    "method": method,
                    "params": params,
                }
            )

        retained = Path("/workspace/.aide-acceptance-proof.json")
        if retained.exists():
            saved = json.loads(retained.read_text())
            reopened = await call("session.open", {"scope": saved["scope"]}, saved["opening_id"])
            assert reopened["session"]["id"] == saved["session_id"]
            replayed = await call("chat.send", saved["params"], saved["request_id"])
            assert replayed == {
                "accepted": True,
                "session_id": saved["session_id"],
                "request_id": saved["request_id"],
            }
            cursor = None
            recovered = []
            while True:
                page = await call(
                    "chat.history",
                    {
                        "session_id": saved["session_id"],
                        "scope": saved["scope"],
                        **({"cursor": cursor} if cursor is not None else {}),
                    },
                )
                recovered.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            original = [item for item in recovered if item.get("request_id") == saved["request_id"]]
            assert len(original) == 1 and original[0]["message"] == saved["params"]["messages"][0]
            print(
                json.dumps(
                    {
                        "event": "native_aide_retained_acceptance",
                        "session_id": saved["session_id"],
                        "request_id": saved["request_id"],
                        "duplicates": 0,
                    }
                )
            )
        opening = str(uuid4())
        session = (await call("session.open", {"scope": scope}, opening))["session"]
        assert session["scope"] == scope, session
        assert (await call("session.open", {"scope": scope}, opening))["session"]["id"] == session[
            "id"
        ]
        params = {
            "scope": scope,
            "session_id": session["id"],
            "messages": [{"role": "user", "content": "Native canonical acceptance proof."}],
        }
        request_id = str(uuid4())
        accepted = await call("chat.send", params, request_id)
        assert accepted == {
            "accepted": True,
            "session_id": session["id"],
            "request_id": request_id,
        }, accepted
        assert await call("chat.send", params, request_id) == accepted
        if not retained.exists():
            retained.write_text(
                json.dumps(
                    {
                        "scope": scope,
                        "opening_id": opening,
                        "session_id": session["id"],
                        "request_id": request_id,
                        "params": params,
                    }
                )
            )
        changed = {**params, "messages": [{"role": "user", "content": "Different."}]}
        assert (await call("chat.send", changed, request_id))["error"]["data"][
            "code"
        ] == "id_reused"
        other = {"scope": "coworld", "coworld": "cow_other", "league": "league_other"}
        mismatch = await call("chat.history", {"scope": other, "session_id": session["id"]})
        assert mismatch["error"]["data"]["code"] == "forbidden", mismatch
        snapshot = await call(
            "state.get", {"resource": "sessions", "id": session["id"], "scope": scope}
        )
        assert snapshot["item"]["active_request_ids"] == [request_id], snapshot
        history = await call("chat.history", {"session_id": session["id"], "scope": scope})
        assert history["items"][-1]["message"] == params["messages"][0], history
        batch = [
            {"role": "system", "content": "Retained context"},
            {"role": "user", "content": "Ordered batch"},
        ]
        batch_id = str(uuid4())
        await call(
            "chat.send", {"session_id": session["id"], "scope": scope, "messages": batch}, batch_id
        )
        history = await call("chat.history", {"session_id": session["id"], "scope": scope})
        assert [item["message"] for item in history["items"][-2:]] == batch, history
        long_batch = [
            {"role": "developer" if i < 54 else "user", "content": f"history-{i}"}
            for i in range(55)
        ]
        await call(
            "chat.send", {"session_id": session["id"], "scope": scope, "messages": long_batch}
        )
        recent = await call("chat.history", {"session_id": session["id"], "scope": scope})
        assert len(recent["items"]) == 50 and recent["items"][-1]["message"] == long_batch[-1]
        assert recent["next_cursor"] is not None
        older = await call(
            "chat.history",
            {"session_id": session["id"], "scope": scope, "cursor": recent["next_cursor"]},
        )
        assert older["next_cursor"] is None
        combined = older["items"] + recent["items"]
        assert [item["message"] for item in combined] == params["messages"] + batch + long_batch
        assert [item["sequence"] for item in combined] == list(range(1, len(combined) + 1))
        large_batches = []
        for batch_index in range(2):
            messages = [
                {
                    "role": "developer" if i < 19 else "user",
                    "content": f"{batch_index}:{i}:" + "x" * (50000 - len(f"{batch_index}:{i}:")),
                }
                for i in range(20)
            ]
            large_batches.extend(messages)
            await call(
                "chat.send", {"session_id": session["id"], "scope": scope, "messages": messages}
            )
        cursor = None
        pages = []
        while True:
            page_id = str(uuid4())
            page = await call(
                "chat.history",
                {
                    "session_id": session["id"],
                    "scope": scope,
                    **({"cursor": cursor} if cursor is not None else {}),
                },
                page_id,
            )
            assert (
                app.size({"jsonrpc": "2.0", "id": page_id, "result": page}) <= app.max_message_bytes
            )
            pages.insert(0, page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert [item["message"] for page in pages for item in page] == params[
            "messages"
        ] + batch + long_batch + large_batches
        oversized_session = (await call("session.open", {"scope": scope}))["session"]["id"]
        giant = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "chat.send",
            "params": {
                "session_id": oversized_session,
                "scope": scope,
                "messages": [{"role": "user", "content": [{"type": "text", "text": ""}]}],
            },
        }
        giant["params"]["messages"][0]["content"][0]["text"] = "x" * (
            app.max_message_bytes - app.size(giant)
        )
        assert app.size(giant) == app.max_message_bytes
        assert (await app.execute(giant))["accepted"]
        oversized = await call("chat.history", {"session_id": oversized_session, "scope": scope})
        assert oversized["error"]["data"]["code"] == "too_large"
        board_scope = {
            "scope": "coworld",
            "coworld": "cow_scope_diplomacy",
            "league": "league_cow_scope_diplomacy",
        }
        board = await call("state.list", {"resource": "tasks", "scope": board_scope})
        assert board["items"], board
        task = board["items"][0]
        individual = await call(
            "state.get", {"resource": "tasks", "scope": board_scope, "id": task["id"]}
        )
        assert individual["item"] == task
        cancellation_id = str(uuid4())
        cancel_params = {"task_id": task["id"], "scope": board_scope}
        cancellation = await call("task.cancel", cancel_params, cancellation_id)
        assert cancellation == {"requested": task["status"] not in {"completed", "abandoned"}}, (
            cancellation
        )
        assert await call("task.cancel", cancel_params, cancellation_id) == cancellation
        print(
            json.dumps(
                {
                    "event": "native_aide_proof",
                    "session_id": session["id"],
                    "accepted_request_id": request_id,
                    "scope": scope,
                }
            )
        )


asyncio.run(main())

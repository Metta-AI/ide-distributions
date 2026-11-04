"""Real pinned plugin callback -> durable inbox -> running packaged coordinator.

Domain data is a fixture. Services, interpreter, pause gate and command application are the
normal image configuration. No model is invoked and no live runtime code is replaced.
Run last: the system harness discards this container and fixture volume afterward.
"""

import json
import os
from urllib.request import Request, urlopen
from uuid import uuid4

from hermes_cli.plugins import discover_plugins

discover_plugins()  # Uses the installed profile shim and its recorded core path.
from tools.registry import registry  # noqa: E402

from research_core import layout  # noqa: E402
from research_core.store import coworlds, settings  # noqa: E402
from research_core.store.actors import Actor  # noqa: E402
from research_core.store.db import Store  # noqa: E402


def post(path, body):
    with urlopen(
        Request(
            "http://127.0.0.1:8650" + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        ),
        timeout=30,
    ) as response:
        result = json.load(response)
        assert result["ok"] if "ok" in result else True, result
        return result


ws = layout.from_env()
actor = Actor.user("packaged-transition-proof")
with Store.open(ws.store_path, kind="workspace") as store:
    # Pausing uses the shipped admission gate, while command application remains available.
    settings.update(store, actor, "paused", True, cites="packaged transition fixture")
    coworld = ws.coworld("transition-proof")
    coworld.ensure_dirs()
    coworlds.register(store, actor, coworld.coworld_id, folder=coworld.folder_name)
    coworlds.activate(store, actor, coworld.coworld_id)

opened = post(
    "/session-open", {"op_id": str(uuid4()), "client": {"id": "packaged-proof", "kind": "test"}}
)
session_id = opened["session_id"]
os.environ["IDE_SESSION_ID"] = session_id
args = {
    "action": "propose",
    "type": "maintenance",
    "title": "Packaged command proof",
    "description": "Verify stable tool intent and coordinator application",
    "operation_id": "packaged-proposal",
}
first = json.loads(registry.dispatch("task", args, session_id=session_id, task_id=session_id))
assert first["ok"] and first["record"]["status"] == "proposed", first
replay = json.loads(registry.dispatch("task", args, session_id=session_id, task_id=session_id))
assert replay == first, (first, replay)
conflict = json.loads(
    registry.dispatch(
        "task", {**args, "title": "Changed"}, session_id=session_id, task_id=session_id
    )
)
assert not conflict["ok"] and "different transition intent" in conflict["error"], conflict
record = first["record"]
approved = post(
    "/store/apply",
    {
        "actor": str(actor),
        "op": "tasks.approve",
        "args": {"task_id": record["id"], "operation_id": "approve-proof"},
    },
)
assert approved["record"]["status"] == "unclaimed", approved
with Store.open(coworld.store_path, kind="coworld") as store:
    assert store.one("SELECT count(*) FROM tasks")[0] == 1
    commands = store.q("SELECT * FROM transition_commands")
    assert len(commands) == 2 and all(row["completed_at"] for row in commands)
    owners = {row["owner_instance"] for row in commands}
    assert len(owners) == 1 and all(owner.startswith("coordinator:") for owner in owners)
    assert store.unmarked_changes() == []
print(
    json.dumps(
        {
            "proof": "packaged_transition_commands",
            "commands": len(commands),
            "tasks": 1,
            "owner_instances": sorted(owners),
            "replay": "same result",
            "payload_conflict": "refused",
            "user_approval": "unclaimed",
        }
    )
)

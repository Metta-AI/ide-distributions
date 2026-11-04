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
from research_core.store import admissions, coworlds, sessions, settings, tasks  # noqa: E402
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
        return json.load(response)


def status():
    with urlopen("http://127.0.0.1:8650/status", timeout=30) as response:
        return json.load(response)


ws = layout.from_env()
actor = Actor.user("packaged-transition-proof")
awake = status()
assert awake["paused"] is False and awake["sleep_started"] is None, awake
owner = awake["coordinator_instance"]
assert owner.startswith("coordinator:"), awake
with Store.open(ws.store_path, kind="workspace") as store:
    coworld = ws.coworld("transition-proof")
    coworld.ensure_dirs()
    coworlds.register(store, actor, coworld.coworld_id, folder=coworld.folder_name)

opened = post(
    "/session-open",
    {
        "op_id": str(uuid4()),
        "client": {"id": "packaged-proof", "kind": "test"},
        "session_context": {
            "contract_version": 1,
            "scope": "coworld",
            "coworld": coworld.coworld_id,
        },
    },
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
still_awake = status()
assert still_awake["paused"] is False and still_awake["sleep_started"] is None, still_awake
with Store.open(ws.store_path, kind="workspace") as store:
    # Close the real admission gate deliberately; command application must stay available.
    settings.update(store, actor, "paused", True, cites="packaged transition fixture")
paused = status()
assert paused["paused"] is True and paused["sleep_started"] is None, paused
approved = post(
    "/store/apply",
    {
        "actor": str(actor),
        "op": "tasks.approve",
        "coworld": coworld.coworld_id,
        "args": {"task_id": record["id"], "operation_id": "approve-proof"},
    },
)
assert approved["record"]["status"] == "unclaimed", approved

# Fixture-backed worker authority: a queued attempt, not an accepted native/model run.
# The normal pause gate prevents dispatch; the installed callback and coordinator stay real.
worker_id = "packaged-report-worker"
worker = Actor.session(worker_id)
task_id = int(record["id"].removeprefix("T-"))
with (
    Store.open(ws.store_path, kind="workspace") as wstore,
    Store.open(coworld.store_path, kind="coworld") as store,
    admissions.dispatch_lock(wstore),
):
    sessions.register(
        wstore,
        actor,
        session_id=worker_id,
        profile="researcher",
        kind="task",
        coworld_id=coworld.coworld_id,
        task_id=task_id,
    )
    attempt_id = admissions.enqueue(
        wstore,
        actor,
        key="packaged-report-attempt",
        session_id=worker_id,
        profile="researcher",
        text="Fixture; do not execute",
        origin="assignment",
    )
    with wstore.tx(actor) as tx:
        tx.execute(
            "INSERT INTO task_assignments"
            " (coworld_id,task_id,op_id,session_id,profile,task_updated,rationale,context)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                coworld.coworld_id,
                task_id,
                attempt_id,
                worker_id,
                "researcher",
                approved["record"]["updated"],
                "Packaged report fixture",
                "No native execution",
            ),
        )
    claimed = tasks.claim(
        store,
        worker,
        task_id,
        run_id=None,
        worktree=str(coworld.worktree_for_task(task_id)),
        branch=f"task/{task_id}",
    )
os.environ["IDE_SESSION_ID"] = worker_id
os.environ["IDE_ACTOR"] = str(worker)
os.environ["IDE_LEASE_EPOCH"] = str(claimed["lease_epoch"])
report_args = {
    "action": "wait",
    "task": record["id"],
    "kind": "user",
    "detail": "Packaged authority proof",
    "operation_id": "packaged-report",
}
reported = json.loads(
    registry.dispatch("task", report_args, session_id=worker_id, task_id=worker_id)
)
assert reported["ok"] and reported["record"]["waiting_on"]["kind"] == "user", reported
report_replay = json.loads(
    registry.dispatch("task", report_args, session_id=worker_id, task_id=worker_id)
)
assert report_replay == reported, (reported, report_replay)
with (
    Store.open(ws.store_path, kind="workspace") as wstore,
    Store.open(coworld.store_path, kind="coworld") as store,
    admissions.dispatch_lock(wstore),
):
    command = store.one(
        "SELECT * FROM transition_commands WHERE issuer_attempt_id=?", (attempt_id,)
    )
    assert command["actor"] == str(worker) and command["actor_epoch"] == claimed["lease_epoch"]
    assert command["completed_at"] and command["owner_instance"] == owner
    assert not wstore.one(
        "SELECT dispatch_started FROM client_admissions WHERE op_id=?", (attempt_id,)
    )[0]
os.environ["IDE_LEASE_EPOCH"] = str(claimed["lease_epoch"] - 1)
refused = json.loads(
    registry.dispatch(
        "task",
        {**report_args, "operation_id": "packaged-late-report"},
        session_id=worker_id,
        task_id=worker_id,
    )
)
assert not refused["ok"] and "lease epoch" in refused["error"], refused
with (
    Store.open(ws.store_path, kind="workspace") as wstore,
    Store.open(coworld.store_path, kind="coworld") as store,
    admissions.dispatch_lock(wstore),
):
    tasks.release(store, worker, task_id, reason="Packaged fixture cleanup")
    with wstore.tx(actor) as tx:
        tx.execute(
            "UPDATE client_admissions SET status='cancelled',cancel_requested=1 WHERE op_id=?",
            (attempt_id,),
        )
        tx.execute("DELETE FROM task_assignments WHERE op_id=?", (attempt_id,))
    sessions.end(wstore, actor, worker_id)

final = status()
assert final["coordinator_instance"] == owner and final["sleep_started"] is None, final
with Store.open(coworld.store_path, kind="coworld") as store:
    fixture_tasks = store.q("SELECT id FROM tasks WHERE title=?", (args["title"],))
    assert [row["id"] for row in fixture_tasks] == [task_id]
    background_tasks = store.q(
        "SELECT type,capability,created_by FROM tasks WHERE id!=?", (task_id,)
    )
    assert all(
        row["type"] == "maintenance"
        and row["capability"] is not None
        and row["created_by"] == "harness"
        for row in background_tasks
    ), [dict(row) for row in background_tasks]
    commands = store.q("SELECT * FROM transition_commands")
    assert len(commands) == 3 and all(row["completed_at"] for row in commands)
    owners = {row["owner_instance"] for row in commands}
    assert owners == {owner}, (owners, final)
    assert store.unmarked_changes() == []
print(
    json.dumps(
        {
            "proof": "packaged_transition_commands",
            "commands": len(commands),
            "fixture_tasks": len(fixture_tasks),
            "background_tasks": len(background_tasks),
            "owner_instances": sorted(owners),
            "replay": "same result",
            "payload_conflict": "refused",
            "user_approval": "unclaimed",
            "awake_precondition": "unpaused, no sleep sign-off",
            "worker_report": "queued fixture attempt and explicit lease epoch",
            "stale_epoch": "refused before command acceptance",
        }
    )
)

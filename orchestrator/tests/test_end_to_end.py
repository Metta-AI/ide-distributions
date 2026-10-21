"""Both distributions on one real Hermes host, with no model and no platform (design T6, local).

Installs ``researcher-1`` and ``orchestrator`` from ``git archive HEAD`` into a throwaway host
root, bootstraps them (the researcher seeds the shared canonical repository, the orchestrator
clones it), then plays one card through: the orchestrator's ``card_create`` makes it, a script
stands in for the researcher's worker (worktree, a record, ``workspace stage``, completion),
and the orchestrator's gate, reconcile and status see the result. The fake Softmax backend
stands in for the platform.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from support.hermes_python import hermes_python

REPO = Path(__file__).resolve().parents[2]
CREDENTIAL = '{"token": "ply_fake", "expires_at": "2099-01-01T00:00:00Z"}'
CLAIM = {
    "kind": "mechanism",
    "status": "belief",
    "confidence": 0.7,
    "evidence": [],
    "revise_if": "A replay shows damage other than 10.",
    "relations": [],
    "topics": ["T-1"],
    "valid_from_version": "65",
    "last_checked_version": "65",
}

DROPPED = {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "PYTHONPATH",
           "SOFTMAX_PLAYER_CREDENTIAL"}  # fmt: skip

pytestmark = pytest.mark.hermes


def clean_env(**extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HERMES_", "RESEARCH_", "AUTORESEARCH_", "OPENROUTER_"))
        and k not in DROPPED
    }
    return {**env, **extra}


def run(argv, env, cwd=None, timeout=600):
    result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True,
                            check=False, timeout=timeout)  # fmt: skip
    assert result.returncode == 0, f"{argv}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result.stdout


def envelope(stdout: str) -> dict:
    payload = json.loads(stdout.strip().splitlines()[-1])
    assert payload["ok"], payload
    return payload["data"]


@pytest.fixture
def host(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "HEAD", "researcher", "orchestrator"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
    root = tmp_path / "host"
    root.mkdir()
    canonical = root / "canonical-fakegame.git"
    hermes = clean_env(HERMES_HOME=str(root))
    profiles = {}
    for name, source in (("researcher-1", "researcher"), ("orchestrator", "orchestrator")):
        run(["hermes", "profile", "install", str(export / source), "--name", name, "--yes"], hermes)
        home = root / "profiles" / name
        values = {
            "SOFTMAX_PLAYER_CREDENTIAL": f"'{CREDENTIAL}'",
            "RESEARCH_PLAYER_ID": "ply_fake",
            "RESEARCH_CANONICAL": str(canonical),
            "RESEARCH_WORKSPACE": str(home / "workspace"),
            "RESEARCH_GAME": "fakegame",
            "RESEARCH_SOFTMAX": "fake",
        }
        (home / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        env = clean_env(HERMES_HOME=str(home), **{k: v.strip("'") for k, v in values.items()})
        profiles[name] = (home, env)
    for name in ("researcher-1", "orchestrator"):  # researchers first: they ship the seed
        home, env = profiles[name]
        run([hermes_python(), str(home / "scripts/bootstrap.py")], env, cwd=home / "scripts")
    run(["hermes", "kanban", "boards", "create", "autoresearch"], hermes)
    return root, profiles


ORCHESTRATOR = """
import json, os, sys
from pathlib import Path
from orchestrator.board import Board
from orchestrator.context import Context
from orchestrator.service import Service
from orchestrator.gate import evaluate
context = Context(Path(os.environ["HERMES_HOME"]), dict(os.environ))
step = sys.argv[1]
if step == "create":
    out = Board(context).card_create(
        command_id="user:1:investigate", assignee="researcher-1",
        title="Find the damage rule", body="Record what damage is.")
elif step == "complete":
    from hermes_cli import kanban_db as kb
    from orchestrator.board import connection
    with connection() as conn:  # what the worker's kanban_complete does
        out = {"changed": kb.complete_task(conn, sys.argv[2], summary="Damage is 10.",
               metadata={"outcome": "done", "merged": json.loads(sys.argv[3])})}
elif step == "gate":
    out = evaluate(context)
elif step == "reconcile":
    out = Service(context).board_reconcile()
elif step == "status":
    out = Service(context).status()
elif step == "ack":
    out = Board(context).event_ack(json.loads(sys.argv[2]))
print(json.dumps({"ok": True, "data": out}, default=str))
"""


def orchestrator(profiles, *args):
    home, env = profiles["orchestrator"]
    env = {**env, "PYTHONPATH": str(home / "src")}
    return envelope(run([hermes_python(), "-c", ORCHESTRATOR, *args], env))


def test_one_card_from_creation_to_the_orchestrators_view(host):
    _, profiles = host
    researcher_home, researcher_env = profiles["researcher-1"]
    clone = researcher_home / "workspace"
    orchestrator_clone = profiles["orchestrator"][0] / "workspace"
    assert (clone / "AGENTS.md").is_file() and (orchestrator_clone / ".git").is_dir()

    card = orchestrator(profiles, "create")["card"]
    assert card["assignee"] == "researcher-1" and card["id"] in card["branch_name"]

    # The researcher's worker: the dispatcher's worktree, one record, stage, complete.
    worktree = Path(card["workspace_path"])
    run(["git", "-C", str(clone), "worktree", "add", str(worktree), card["branch_name"]],
        researcher_env)  # fmt: skip
    cli = str(researcher_home / "bin" / "research")
    worker_env = {**researcher_env, "HERMES_KANBAN_WORKSPACE": str(worktree)}
    claim = envelope(
        run(
            [
                cli,
                "records",
                "new",
                "claim",
                "--meta",
                json.dumps(CLAIM),
                "--body",
                "Damage is 10.",
                "--root",
                str(worktree),
            ],
            worker_env,
        )
    )["id"]
    staged = envelope(run([cli, "workspace", "stage"], worker_env, cwd=worktree))
    orchestrator(profiles, "complete", card["id"], json.dumps(staged))
    coordinator_home, coordinator_env = profiles["orchestrator"]
    envelope(run([str(coordinator_home / "bin/research"), "workspace", "integrate",
                  "--branch", staged["branch"], "--commit", staged["card_sha"]], coordinator_env))
    run(["git", "-C", str(clone), "pull", "--ff-only"], researcher_env)

    wake = orchestrator(profiles, "gate")
    assert wake["wakeAgent"] is True
    kinds = {event.get("kind") for event in wake["context"]["events"]}
    assert "completed" in kinds and "claimed" not in kinds

    view = orchestrator(profiles, "reconcile")
    assert view["cards"]  # execution state is independent of commit integration
    status = orchestrator(profiles, "status")
    assert status["cards"] == {"done": [card["id"]]}
    assert status["promotion"]["conflict"] is None  # a fresh fake league, no champion yet
    found = envelope(run([cli, "records", "find", "claim"], researcher_env))["ids"]
    assert claim in found

    orchestrator(profiles, "ack", json.dumps(
        [{"table": e["table"], "id": e["id"]} for e in view["events"]]))  # fmt: skip
    assert orchestrator(profiles, "gate") == {"wakeAgent": False}

"""``autoresearch softmax ...`` through the real entry point (subprocess), with
AUTORESEARCH_SOFTMAX=fake, the fakegame contract and a FileQueue (Plan 03 Task 3.4)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.promotion_board import BASELINE_PV, LEAGUE, PLAYER, PV_A, SubmitBoard

from autoresearch.cli import main
from autoresearch.io.docs import read_json
from autoresearch.paths import Layout
from autoresearch.queue.select import FILE_QUEUE_RELATIVE
from autoresearch.records.store import RecordStore
from autoresearch.runtime.promotion import CHAMPION_PATH, load
from autoresearch.softmax_tool import cli as softmax_cli
from autoresearch.softmax_tool.fake import FAULTS_ENV
from autoresearch.softmax_tool.platform_cli import PlatformCli, ToolMissing
from autoresearch.softmax_tool.xp import parse_tag

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def board(tmp_path):
    queue_dir = tmp_path / "canonical" / ".supervisor" / "runtime" / FILE_QUEUE_RELATIVE
    return SubmitBoard(tmp_path, queue_dir=queue_dir)


def base_env(board, worker=None, **extra) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HERMES_HOME": str(board.tmp_path / "home"),
        "AUTORESEARCH_WORKSPACE": str(board.canonical),
        "AUTORESEARCH_SOFTMAX": "fake",
        "AUTORESEARCH_GAME": "fakegame",
        "AUTORESEARCH_QUEUE": "file",
        "AUTORESEARCH_PLAYER_ID": PLAYER,
    }
    if worker is not None:
        env.update(
            HERMES_KANBAN_WORKSPACE=str(worker.worktree),
            HERMES_KANBAN_TASK=worker.task_id,
            HERMES_KANBAN_RUN_ID=worker.run_id,
        )
    env.update(extra)
    return env


def softmax(env, *argv):
    proc = subprocess.run(
        [sys.executable, "-m", "autoresearch", "softmax", *argv],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.stdout.strip(), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def uploaded_experiment(store: RecordStore, design: dict) -> str:
    from records.fixtures import EXP_DESIGNED

    experiment = store.create("experiment", {**EXP_DESIGNED, "evaluation_design": design})
    store.set_status(experiment, "built")
    binding = {"version_id": "V-1", "platform_policy_version_id": PV_A}
    store.update(experiment, {"status": "uploaded", "candidate_binding": binding})
    return experiment


@pytest.fixture
def launch(board):
    """A launch card's worker with an uploaded, bound experiment in its worktree."""
    from support.promotion_board import design_dict

    worker = board.admit(mode="experimentation")
    worker.start()
    experiment = uploaded_experiment(worker.store, design_dict())
    return worker, experiment


# --- identity and account ---


def test_whoami(board):
    code, env = softmax(base_env(board), "whoami")
    assert code == 0 and env["ok"] is True
    assert env["data"]["subject_type"] == "player" and env["data"]["player_id"] == PLAYER
    assert env["data"]["problems"] == []


def test_whoami_reports_identity_problems(board):
    code, env = softmax(base_env(board, **{FAULTS_ENV: "identity=user"}), "whoami")
    assert code == 1 and env["error"]["reason"] == "identity"


def test_credits_fake(board):
    code, env = softmax(base_env(board), "credits")
    assert code == 0 and env["data"] == {"available": True, "credits": 100}


def test_credits_real_client_is_unavailable(board):
    env = base_env(board)
    del env["AUTORESEARCH_SOFTMAX"]
    code, data = softmax(env, "credits")
    assert code == 0 and data["data"]["available"] is False
    assert "Player" in data["data"]["reason"]


def test_missing_tool_is_exit_3(board, monkeypatch, capsys, tmp_path):
    for name, value in base_env(board).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AUTORESEARCH_SOFTMAX")
    monkeypatch.setenv(
        "SOFTMAX_PLAYER_CREDENTIAL", '{"token": "t", "expires_at": "2099-01-01T00:00:00+00:00"}'
    )
    real_init = PlatformCli.__init__

    def empty_bin(self, env, run=subprocess.run, bin_dir=None):
        real_init(self, env, run=run, bin_dir=tmp_path / "empty-bin")

    monkeypatch.setattr(PlatformCli, "__init__", empty_bin)
    code = main(["softmax", "whoami"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 3 and payload["error"]["reason"] == "tool_missing"


def test_platform_cli_maps_missing_executable(tmp_path):
    cli = PlatformCli(
        {
            "HERMES_HOME": str(tmp_path),
            "SOFTMAX_PLAYER_CREDENTIAL": '{"token": "t", "expires_at": "2099-01-01T00:00:00+00:00"}',
        },
        bin_dir=tmp_path / "nowhere",
    )
    with pytest.raises(ToolMissing):
        cli.run("coworld", ["leagues"])


def test_no_profile_context_is_exit_2():
    code, env = softmax({"PATH": os.environ.get("PATH", "")}, "whoami")
    assert code == 2 and env["error"]["reason"] == "bad_arguments"


# --- XP ---


def test_xp_spec_maps_design_to_the_platform_roster():
    from support.promotion_board import design_dict

    design = design_dict()  # seats (0, 1), opponents ("random", "opp:v3"), n_min 12
    binding = {"platform_policy_version_id": PV_A}
    spec, side = softmax_cli.xp_spec(design, binding, LEAGUE, batch=1, batches=2)
    assert side == "candidate"
    assert spec["league_id"] == LEAGUE and spec["num_episodes"] == 6
    assert spec["roster"] == [
        {"slot": 0, "player": {"policy_ref": PV_A}},
        {"slot": 1, "player": {"policy_ref": PV_A}},
        {"slot": -1, "player": {"random": True}},
        {"slot": -1, "player": {"policy_ref": "opp:v3"}},
    ]
    spec, side = softmax_cli.xp_spec(design, binding, LEAGUE, batch=2, batches=2)
    assert side == "baseline"
    assert spec["roster"][0] == {"slot": 0, "player": {"policy_ref": BASELINE_PV}}
    spec, _ = softmax_cli.xp_spec(design, binding, LEAGUE, batch=1, batches=5)
    assert spec["num_episodes"] == 3  # ceil(12 / 5)


def test_credit_backoff_doubles_and_caps():
    minutes = [softmax_cli.credit_backoff(n).total_seconds() / 60 for n in range(1, 8)]
    assert minutes == [30, 60, 120, 240, 480, 480, 480]


def test_xp_create_records_request_and_starts_evaluating(board, launch):
    worker, experiment = launch
    env = base_env(board, worker)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0, data
    request = data["data"]
    assert request["side"] == "candidate" and request["request_id"].startswith("xp_")
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "evaluating"
    assert meta["xp_requests"] == [
        {
            "batch": 1,
            "attempt": 1,
            "request_id": request["request_id"],
            "side": "candidate",
            "policy": PV_A,
        }
    ]
    # retried create: the same request, no second xp_requests entry
    code, again = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and again["data"]["request_id"] == request["request_id"]
    assert len(worker.store.load(experiment)[0]["xp_requests"]) == 1

    code, recovered = softmax(env, "xp", "recover", experiment)
    assert code == 0
    assert [h["request_id"] for h in recovered["data"]] == [request["request_id"]]

    code, status = softmax(env, "xp", "status", experiment)
    assert code == 0 and status["data"][0]["state"] == "completed"
    assert status["data"][0]["episodes_total"] == 12

    dest = board.tmp_path / "artifacts"
    code, artifacts = softmax(env, "xp", "artifacts", experiment, "--dest", str(dest))
    assert code == 0
    (paths,) = artifacts["data"].values()
    assert Path(paths[0]).exists() and Path(paths[0]).is_relative_to(dest)


def test_xp_create_credit_denied_sets_awaiting_credits_with_backoff(board, launch):
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "credits=0"})
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and data["data"]["credit_denied"] is True
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "awaiting-credits"
    assert meta["lifecycle"]["credit_denials"] == 1
    first = meta["lifecycle"]["next_launch_after"]
    assert data["data"]["next_launch_after"] == first

    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "2")
    assert code == 0 and data["data"]["credit_denied"] is True
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "awaiting-credits" and meta["lifecycle"]["credit_denials"] == 2
    assert meta["lifecycle"]["next_launch_after"] > first

    code, data = softmax(
        base_env(board, worker), "xp", "create", experiment, "--batch", "1", "--attempt", "3"
    )
    assert code == 0 and data["data"]["side"] == "candidate"
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "evaluating"
    assert meta["lifecycle"]["credit_denials"] == 0
    assert meta["lifecycle"]["next_launch_after"] is None


def test_xp_create_identity_fault_is_refused(board, launch):
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "player=ply_other"})
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 1 and data["error"]["reason"] == "identity"
    assert worker.store.load(experiment)[0]["status"] == "uploaded"


def test_xp_create_needs_a_binding(board):
    from records.fixtures import EXP_DESIGNED

    worker = board.admit(mode="experimentation")
    worker.start()
    experiment = worker.store.create("experiment", dict(EXP_DESIGNED))
    code, data = softmax(
        base_env(board, worker), "xp", "create", experiment, "--batch", "1", "--attempt", "1"
    )
    assert code == 1 and data["error"]["reason"] == "no_binding"


# --- upload, submit, leaderboard ---


def test_upload_from_fakegame_build(board):
    from records.fixtures import EXP_DESIGNED

    worker = board.admit(mode="experimentation")
    worker.start()
    experiment = worker.store.create("experiment", dict(EXP_DESIGNED))
    worker.store.set_status(experiment, "built")
    build_dir = board.tmp_path / "build"
    tool = REPO / "games" / "fakegame" / "tools" / "fake_build.py"
    # the worktree's strategy/STRATEGY.md is the champion seeded by SubmitBoard
    subprocess.run(
        [sys.executable, str(tool), str(worker.worktree), "--out", str(build_dir), "--json"],
        check=True,
        capture_output=True,
    )
    env = base_env(board, worker)
    code, data = softmax(env, "upload", experiment, "--build-dir", str(build_dir))
    assert code == 0, data
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "uploaded"
    assert meta["candidate_binding"] == {
        "version_id": data["data"]["version_id"],
        "platform_policy_version_id": data["data"]["platform_policy_version_id"],
    }


def test_upload_failed_gates_is_refused(board):
    from records.fixtures import EXP_DESIGNED

    worker = board.admit(mode="experimentation")
    worker.start()
    experiment = worker.store.create("experiment", dict(EXP_DESIGNED))
    worker.store.set_status(experiment, "built")
    build_dir = board.tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "p.json").write_text("{}")
    (build_dir / "version.json").write_text(
        json.dumps(
            {"artifact": "p.json", "strategy_sha256": "a" * 64, "gates": {"G1": {"passed": False}}}
        )
    )
    code, data = softmax(
        base_env(board, worker), "upload", experiment, "--build-dir", str(build_dir)
    )
    assert code == 1 and data["error"]["reason"] == "gates_failed"


def test_submit_end_to_end(board):
    experiment, version = board.reviewed_experiment()
    worker = board.promoter(experiment)
    code, data = softmax(base_env(board, worker), "submit", experiment)
    assert code == 0, data
    assert data["data"]["platform_policy_version_id"] == PV_A
    assert load(board.canonical).stage == "submitted"
    assert (worker.worktree / CHAMPION_PATH).read_text().startswith("# Candidate")
    store = RecordStore(Layout(worker.worktree))
    assert store.find("version_event", version=version, kind="submitted")


def test_submit_refusal_is_exit_1_with_reason(board):
    experiment, _ = board.reviewed_experiment(decision="hold")
    worker = board.promoter(experiment)
    code, data = softmax(base_env(board, worker), "submit", experiment)
    assert code == 1 and data["ok"] is False
    assert data["error"]["reason"] == "decision_not_promote"
    assert load(board.canonical) is None


def test_submit_identity_fault_is_exit_1(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    env = base_env(board, worker, **{FAULTS_ENV: "player=ply_other"})
    code, data = softmax(env, "submit", experiment)
    assert code == 1 and data["error"]["reason"] == "identity"
    ledger = board.canonical / ".supervisor" / "ledger" / "promotion.json"
    assert not ledger.exists() or read_json(ledger)["stage"] != "intent"


def test_leaderboard(board):
    code, data = softmax(base_env(board), "leaderboard")
    assert code == 0
    assert data["data"] == {
        "league_id": LEAGUE,
        "player_id": PLAYER,
        "leaderboard_champion": BASELINE_PV,
        "current_champion": BASELINE_PV,
        "note": "",
    }


def test_xp_tag_round_trip():
    assert parse_tag("ar:E-1:b2:a3") == ("E-1", 2, 3)

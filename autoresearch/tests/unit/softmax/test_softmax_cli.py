"""``autoresearch softmax ...`` through the real entry point (subprocess), with
AUTORESEARCH_SOFTMAX=fake, the fakegame contract and a FileQueue (Plan 03 Task 3.4)."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support.promotion_board import BASELINE_PV, LEAGUE, PLAYER, PV_A, SubmitBoard

from autoresearch.cli import main
from autoresearch.io.docs import read_json, write_json
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


def test_xp_specs_one_seeded_request_per_seed_and_arm():
    """A12: each seed in the batch's slice gets one candidate and one baseline request with
    num_episodes 1 and game_config_overrides.seed; opponents sit in fixed slots."""
    from support.promotion_board import design_dict

    design = {**design_dict(), "seeds": [11, 12, 13, 14, 15]}  # seats (0, 1)
    binding = {"platform_policy_version_id": PV_A}
    first = softmax_cli.xp_specs(design, binding, LEAGUE, batch=1, batches=2)
    assert [(seed, arm) for seed, arm, _ in first] == [
        (11, "candidate"),
        (11, "baseline"),
        (12, "candidate"),
        (12, "baseline"),
        (13, "candidate"),
        (13, "baseline"),
    ]
    second = softmax_cli.xp_specs(design, binding, LEAGUE, batch=2, batches=2)
    assert [seed for seed, arm, _ in second if arm == "candidate"] == [14, 15]
    _, _, candidate = first[0]
    _, _, baseline = first[1]
    assert candidate == {
        "league_id": LEAGUE,
        "num_episodes": 1,
        "game_config_overrides": {"seed": 11},
        "roster": [
            {"slot": 0, "player": {"policy_ref": PV_A}},
            {"slot": 1, "player": {"policy_ref": PV_A}},
            {"slot": 2, "player": {"random": True}},
            {"slot": 3, "player": {"policy_ref": "opp:v3"}},
        ],
    }
    assert baseline["roster"][:2] == [
        {"slot": 0, "player": {"policy_ref": BASELINE_PV}},
        {"slot": 1, "player": {"policy_ref": BASELINE_PV}},
    ]
    assert baseline["roster"][2:] == candidate["roster"][2:]
    assert baseline["game_config_overrides"] == {"seed": 11}


def test_xp_specs_opponent_slots_skip_our_seats():
    from support.promotion_board import design_dict

    design = {**design_dict(), "seats": [1], "opponents": ["opp:v3", "opp:v4"]}
    (_, _, spec), _ = softmax_cli.xp_specs(
        design, {"platform_policy_version_id": PV_A}, LEAGUE, 1, 2
    )
    assert [entry["slot"] for entry in spec["roster"]] == [1, 0, 2]


def test_xp_specs_refuses_bad_batches_and_seedless_designs():
    from support.promotion_board import design_dict

    binding = {"platform_policy_version_id": PV_A}
    with pytest.raises(softmax_cli.BadArguments, match="batch"):
        softmax_cli.xp_specs(design_dict(), binding, LEAGUE, batch=3, batches=2)
    with pytest.raises(softmax_cli.BadArguments, match="batch"):
        softmax_cli.xp_specs(design_dict(), binding, LEAGUE, batch=0, batches=2)
    five = {**design_dict(), "seeds": [1, 2, 3, 4, 5]}  # slices of 2: batch 4 is empty
    with pytest.raises(softmax_cli.Refused) as refused:
        softmax_cli.xp_specs(five, binding, LEAGUE, batch=4, batches=4)
    assert refused.value.reason == "empty_batch"


def test_design_pairing_is_seed_only_with_selector_opponents():
    from support.promotion_board import design_dict

    from autoresearch.stats.design import EvaluationDesign

    assert EvaluationDesign.from_dict(design_dict()).pairing == "seed_only"
    pinned = {**design_dict(), "opponents": ["opp:v3"]}
    assert EvaluationDesign.from_dict(pinned).pairing == "seed_and_opponents"


def test_credit_backoff_doubles_and_caps():
    minutes = [softmax_cli.credit_backoff(n).total_seconds() / 60 for n in range(1, 8)]
    assert minutes == [30, 60, 120, 240, 480, 480, 480]


def test_credit_backoff_base_comes_from_the_environment(board, launch):
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "credits=0", softmax_cli.BACKOFF_ENV: "2"})
    before = datetime.now(UTC)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and data["data"]["credit_denied"] is True
    after = datetime.fromisoformat(data["data"]["next_launch_after"])
    assert timedelta(minutes=1) < after - before < timedelta(minutes=3)
    bad = base_env(board, worker, **{softmax_cli.BACKOFF_ENV: "soon"})
    code, data = softmax(bad, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 2 and data["error"]["reason"] == "bad_arguments"
    assert worker.store.load(experiment)[0]["xp_requests"] == []  # refused before any create


def test_xp_create_records_request_and_starts_evaluating(board, launch):
    worker, experiment = launch  # design_dict: seeds (1, 2)
    env = base_env(board, worker)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0, data
    launched = data["data"]
    assert launched["credit_denied"] is False and launched["missing"] == []
    assert launched["pairing"] == "seed_only"
    assert [(r["seed"], r["arm"]) for r in launched["created"]] == [
        (1, "candidate"),
        (1, "baseline"),
        (2, "candidate"),
        (2, "baseline"),
    ]
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "evaluating"
    assert meta["xp_requests"] == launched["created"]
    first = meta["xp_requests"][0]
    assert first == {
        "batch": 1,
        "attempt": 1,
        "seed": 1,
        "arm": "candidate",
        "request_id": first["request_id"],
        "policy": PV_A,
    }
    assert meta["xp_requests"][1]["policy"] == BASELINE_PV
    # retried create: every request exists, nothing new is created or recorded
    code, again = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and again["data"]["created"] == []
    assert len(again["data"]["existing"]) == 4
    assert len(worker.store.load(experiment)[0]["xp_requests"]) == 4

    code, recovered = softmax(env, "xp", "recover", experiment)
    assert code == 0
    assert {(h["seed"], h["arm"]) for h in recovered["data"]} == {
        (1, "candidate"),
        (1, "baseline"),
        (2, "candidate"),
        (2, "baseline"),
    }

    code, status = softmax(env, "xp", "status", experiment)
    assert code == 0 and {row["state"] for row in status["data"]} == {"completed"}
    assert {row["episodes_total"] for row in status["data"]} == {1}

    dest = board.tmp_path / "artifacts"
    code, artifacts = softmax(env, "xp", "artifacts", experiment, "--dest", str(dest))
    assert code == 0 and len(artifacts["data"]) == 4
    for paths in artifacts["data"].values():
        assert Path(paths[0]).exists() and Path(paths[0]).is_relative_to(dest)


def _set_fake_credits(board, credits: int) -> None:
    from autoresearch.io.docs import write_json
    from autoresearch.softmax_tool.fake import FakeSoftmax

    fake = FakeSoftmax(board.canonical, {})
    path = board.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = read_json(path) if path.exists() else fake._initial_state()
    state["credits"] = credits
    write_json(path, state)


def test_xp_create_credit_denied_mid_batch_reports_missing_and_relaunch_fills_them(board, launch):
    worker, experiment = launch
    env = base_env(board, worker)
    _set_fake_credits(board, 3)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and data["data"]["credit_denied"] is True
    assert [(r["seed"], r["arm"]) for r in data["data"]["created"]] == [
        (1, "candidate"),
        (1, "baseline"),
        (2, "candidate"),
    ]
    assert data["data"]["missing"] == [{"seed": 2, "arm": "baseline"}]
    meta = worker.store.load(experiment)[0]
    assert len(meta["xp_requests"]) == 3
    assert meta["status"] == "awaiting-credits" and meta["lifecycle"]["credit_denials"] == 1

    _set_fake_credits(board, 100)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and data["data"]["credit_denied"] is False
    assert [(r["seed"], r["arm"]) for r in data["data"]["created"]] == [(2, "baseline")]
    assert len(data["data"]["existing"]) == 3 and data["data"]["missing"] == []
    meta = worker.store.load(experiment)[0]
    assert len(meta["xp_requests"]) == 4 and meta["status"] == "evaluating"
    assert meta["lifecycle"]["credit_denials"] == 0


def test_xp_create_refuses_a_new_attempt_while_the_last_is_incomplete(board, launch):
    """Fake cycle run 2: after a credit denial the agent relaunched with --attempt 2 and
    repeated every request. A new attempt must wait until the latest one is complete."""
    worker, experiment = launch  # seeds (1, 2): four requests per attempt
    env = base_env(board, worker)
    _set_fake_credits(board, 3)
    softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    _set_fake_credits(board, 100)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "2")
    assert code == 1 and data["error"]["reason"] == "attempt_incomplete", data
    assert "--attempt 1" in data["error"]["message"]
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "3")
    assert code == 1 and data["error"]["reason"] == "bad_attempt", data
    assert len(worker.store.load(experiment)[0]["xp_requests"]) == 3  # nothing new
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and [(r["seed"], r["arm"]) for r in data["data"]["created"]] == [
        (2, "baseline")
    ]
    # A second relauncher after the first completed the attempt: nothing failed, so refused.
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "2")
    assert code == 1 and data["error"]["reason"] == "attempt_not_needed", data
    assert len(worker.store.load(experiment)[0]["xp_requests"]) == 4


def data_first_request(worker, experiment) -> str:
    return worker.store.load(experiment)[0]["xp_requests"][0]["request_id"]


def _fail_fake_request(board, request_id: str) -> None:
    path = board.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = read_json(path)
    state["xp"][request_id]["failed"] = True
    write_json(path, state)


def test_xp_create_new_attempt_only_after_a_failed_request(board, launch):
    worker, experiment = launch  # seeds (1, 2): four requests per attempt
    env = base_env(board, worker)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and len(data["data"]["created"]) == 4
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "2")
    assert code == 1 and data["error"]["reason"] == "attempt_not_needed", data
    failed = data_first_request(worker, experiment)
    _fail_fake_request(board, failed)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "2")
    assert code == 0 and len(data["data"]["created"]) == 4, (failed, data)
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "3")
    assert code == 1 and data["error"]["reason"] == "attempt_not_needed", data


def test_credit_backoff_rejects_infinite_minutes(board, launch):
    worker, experiment = launch
    for value in ("inf", "1e400", "nan"):
        env = base_env(board, worker, **{softmax_cli.BACKOFF_ENV: value})
        code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
        assert code == 2 and data["error"]["reason"] == "bad_arguments", (value, data)


def test_xp_create_credit_denied_sets_awaiting_credits_with_backoff(board, launch):
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "credits=0"})
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0 and data["data"]["credit_denied"] is True
    assert data["data"]["created"] == [] and len(data["data"]["missing"]) == 4
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
    assert code == 0 and len(data["data"]["created"]) == 4
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "evaluating"
    assert meta["lifecycle"]["credit_denials"] == 0
    assert meta["lifecycle"]["next_launch_after"] is None


def test_xp_create_failure_mid_batch_keeps_created_records_and_relaunch_completes(board, launch):
    """A non-credit failure (here the platform's pending-request cap) on the 3rd create: the
    two requests already created are recorded; a relaunch creates the rest and records all."""
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "xp_limit=2"})
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 1 and data["error"]["reason"] == "platform"
    meta = worker.store.load(experiment)[0]
    assert [(r["seed"], r["arm"]) for r in meta["xp_requests"]] == [
        (1, "candidate"),
        (1, "baseline"),
    ]
    code, data = softmax(
        base_env(board, worker), "xp", "create", experiment, "--batch", "1", "--attempt", "1"
    )
    assert code == 0, data
    assert [(r["seed"], r["arm"]) for r in data["data"]["created"]] == [
        (2, "candidate"),
        (2, "baseline"),
    ]
    meta = worker.store.load(experiment)[0]
    assert len(meta["xp_requests"]) == 4 and meta["status"] == "evaluating"


def test_xp_create_backfills_requests_created_but_never_recorded(board, launch):
    """A crash right after the platform created a request (before the record write): the
    relaunch finds it by recover, records it, and creates only the rest."""
    worker, experiment = launch
    env = base_env(board, worker, **{FAULTS_ENV: "fail_after=xp_create"})
    code, _ = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 1
    assert not worker.store.load(experiment)[0].get("xp_requests")
    code, data = softmax(
        base_env(board, worker), "xp", "create", experiment, "--batch", "1", "--attempt", "1"
    )
    assert code == 0, data
    assert [(h["seed"], h["arm"]) for h in data["data"]["existing"]] == [(1, "candidate")]
    assert len(data["data"]["created"]) == 3
    entries = worker.store.load(experiment)[0]["xp_requests"]
    assert sorted((r["seed"], r["arm"]) for r in entries) == [
        (1, "baseline"),
        (1, "candidate"),
        (2, "baseline"),
        (2, "candidate"),
    ]
    backfilled = next(r for r in entries if (r["seed"], r["arm"]) == (1, "candidate"))
    assert backfilled["policy"] == PV_A


def test_xp_create_empty_batch_is_refused_without_status_change(board):
    from support.promotion_board import design_dict

    worker = board.admit(mode="experimentation")
    worker.start()
    experiment = uploaded_experiment(worker.store, {**design_dict(), "seeds": [1, 2, 3, 4, 5]})
    code, data = softmax(
        base_env(board, worker),
        "xp",
        "create",
        experiment,
        "--batch",
        "4",
        "--attempt",
        "1",
        "--batches",
        "4",
    )
    assert code == 1 and data["error"]["reason"] == "empty_batch"
    meta = worker.store.load(experiment)[0]
    assert meta["status"] == "uploaded" and not meta.get("xp_requests")


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


# --- per-experiment lock (two relaunchers at the same moment) ---

HOLD_LOCK = """
import sys
from autoresearch.io.locks import file_lock
with file_lock(sys.argv[1], timeout_s=10) as handle:
    if len(sys.argv) > 2:  # the holder record, as experiment_lock writes it
        handle.write(sys.argv[2])
        handle.flush()
    print("held", flush=True)
    sys.stdin.read()  # hold until the test closes stdin
"""


def _hold_lock_in_another_process(path, holder_record=None):
    extra = [json.dumps(holder_record)] if holder_record else []
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(path), *extra],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert holder.stdout.readline().strip() == "held"
    return holder


def _lock_path(board, experiment):
    return board.canonical / ".supervisor" / "runtime" / "locks" / f"xp-{experiment}.lock"


@pytest.mark.parametrize(
    "argv",
    [
        ["xp", "create", "{E}", "--batch", "1", "--attempt", "1"],
        ["upload", "{E}", "--build-dir", "/nonexistent"],
        ["submit", "{E}"],
    ],
    ids=["xp-create", "upload", "submit"],
)
def test_mutations_refuse_busy_while_another_card_holds_the_experiment(
    board, launch, monkeypatch, capsys, argv
):
    """``xp create``, ``upload`` and ``submit`` of one experiment take one cross-process lock
    first: a second process waits a bounded time, then refuses with ``busy``, names the
    holder, and does nothing."""
    worker, experiment = launch
    for name, value in base_env(board, worker).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(softmax_cli, "LOCK_WAIT_S", 0.2)
    argv = [word.format(E=experiment) for word in argv]
    record = {"task_id": "t_peer", "run_id": "41", "pid": 999, "command": "xp create",
              "started_at": "2026-10-08T12:00:00+00:00"}
    holder = _hold_lock_in_another_process(_lock_path(board, experiment), record)
    try:
        code = main(["softmax", *argv])
        payload = json.loads(capsys.readouterr().out)
        error = payload["error"]
        assert code == 1 and error["reason"] == "busy", payload
        assert error["details"] == {"holder": record, "own_run": False}
        assert experiment in error["message"] and "t_peer" in error["message"]
        assert "queue list --state claimed" in error["message"]
        assert worker.store.load(experiment)[0].get("xp_requests") in (None, [])
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)


def test_busy_on_your_own_runs_lock_says_wait_for_that_command(board, launch, monkeypatch,
                                                               capsys):
    """A long ``xp create`` can go to the background (Hermes promotes long terminal calls);
    a rerun from the same card must not read the busy as a peer and give up."""
    worker, experiment = launch
    for name, value in base_env(board, worker).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(softmax_cli, "LOCK_WAIT_S", 0.2)
    record = {"task_id": worker.task_id, "run_id": worker.run_id, "pid": 999,
              "command": "xp create", "started_at": "2026-10-08T12:00:00+00:00"}
    holder = _hold_lock_in_another_process(_lock_path(board, experiment), record)
    try:
        code = main(["softmax", "xp", "create", experiment, "--batch", "1", "--attempt", "1"])
        error = json.loads(capsys.readouterr().out)["error"]
        assert code == 1 and error["reason"] == "busy" and error["details"]["own_run"] is True
        assert "your own earlier" in error["message"] and "wait" in error["message"]
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)


def test_the_holder_is_recorded_while_held_and_cleared_after(board, launch, monkeypatch):
    worker, experiment = launch
    for name, value in base_env(board, worker).items():
        monkeypatch.setenv(name, value)
    from autoresearch import runctx

    ctx = runctx.from_env()
    lock = _lock_path(board, experiment)
    with softmax_cli.experiment_lock(ctx, experiment, "upload"):
        record = json.loads(lock.read_text())
        assert record.pop("started_at")
        assert record == {"task_id": worker.task_id, "run_id": worker.run_id,
                          "pid": os.getpid(), "command": "upload"}
    assert lock.read_text() == ""


def test_xp_create_waits_for_the_other_process_then_runs(board, launch, monkeypatch, capsys):
    import threading

    from autoresearch.io.locks import file_lock

    worker, experiment = launch
    for name, value in base_env(board, worker).items():
        monkeypatch.setenv(name, value)
    lock = _lock_path(board, experiment)
    holder = _hold_lock_in_another_process(lock)
    release = threading.Timer(0.5, holder.stdin.close)  # held when the command starts
    release.start()
    started = time.monotonic()
    code = main(["softmax", "xp", "create", experiment, "--batch", "1", "--attempt", "1"])
    waited = time.monotonic() - started
    release.join()
    holder.wait(timeout=10)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and len(payload["data"]["created"]) == 4, payload
    assert waited >= 0.4
    with file_lock(lock, timeout_s=0):  # not held after the command
        pass


def test_two_concurrent_launches_create_one_set_of_requests(board, launch):
    """The headline guarantee: two relaunchers at the same moment, one set of requests."""
    worker, experiment = launch
    env = base_env(board, worker)
    argv = [sys.executable, "-m", "autoresearch", "softmax", "xp", "create", experiment,
            "--batch", "1", "--attempt", "1"]
    procs = [
        subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    results = [json.loads(proc.communicate(timeout=120)[0]) for proc in procs]
    assert all(result["ok"] for result in results), results
    assert sorted(len(result["data"]["created"]) for result in results) == [0, 4]
    code, recovered = softmax(env, "xp", "recover", experiment)
    assert code == 0 and len(recovered["data"]) == 4


def test_an_experiment_id_that_is_not_an_e_id_is_a_bad_argument(board, launch):
    worker, _ = launch
    code, data = softmax(
        base_env(board, worker), "xp", "create", "../../x", "--batch", "1", "--attempt", "1"
    )
    assert code == 2 and data["error"]["reason"] == "bad_arguments"
    assert not (board.canonical / ".supervisor" / "runtime" / "locks").exists()


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


def test_submit_refused_when_the_profile_identity_changed_since_spawn(board):
    """A worker's environment is fixed when Hermes spawns it, but the profile ``.env`` may
    change while it runs (the operator sets another player id; preflight then pauses). The
    tool keeps using the spawn-time identity and refuses while the ``.env`` disagrees (C6,
    C7); it never takes identity from the ``.env``, which a worker could write."""
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    credential = json.dumps({"token": "t", "expires_at": "2099-01-01T00:00:00Z",
                             "player_id": PLAYER})  # fmt: skip
    home = board.tmp_path / "home"
    home.mkdir()
    env = base_env(board, worker, SOFTMAX_PLAYER_CREDENTIAL=credential)  # spawn-time env
    (home / ".env").write_text(f"AUTORESEARCH_PLAYER_ID={PLAYER}\nOTHER=1\n")
    code, data = softmax(env, "whoami")
    assert code == 0, data  # the .env agrees: no refusal

    (home / ".env").write_text("AUTORESEARCH_PLAYER_ID=ply_wrong\nOTHER=1\n")
    code, data = softmax(env, "submit", experiment)
    assert code == 1 and data["error"]["reason"] == "identity", data
    assert "AUTORESEARCH_PLAYER_ID" in data["error"]["message"]
    assert "t" not in data["error"]["message"].split()  # no value is printed
    assert load(board.canonical) is None  # refused before the promotion lock

    # A worker that rewrites the .env to another credential gains nothing: refused too.
    forged = json.dumps({"token": "x", "expires_at": "2099-01-01T00:00:00Z",
                         "player_id": "ply_other"})  # fmt: skip
    (home / ".env").write_text(f"SOFTMAX_PLAYER_CREDENTIAL='{forged}'\n")
    code, data = softmax(env, "submit", experiment)
    assert code == 1 and data["error"]["reason"] == "identity", data


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
    assert parse_tag("ar:E-1:b2:a3:s1:candidate") == ("E-1", 2, 3, 1, "candidate")

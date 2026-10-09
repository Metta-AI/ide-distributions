"""First champion on a fresh install (amendment A14): ``softmax bootstrap-champion``.

With no champion (no published promotion and no leaderboard entry for our player, A9), a card
builds the game's starter policy, uploads it, and promotes it through the normal C8
promotion (acquire -> intent -> submit -> recorded -> published) as a ``bootstrap`` promotion:
no experiment, no evaluation outcome, no decision. Experiments then compare against it.
"""

import json
import subprocess
import sys

import pytest
from support.promotion_board import LEAGUE, PLAYER, SubmitBoard

from autoresearch.comms.resolver import resolve
from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.queue.select import FILE_QUEUE_RELATIVE
from autoresearch.records.store import RecordStore
from autoresearch.runtime import promotion
from autoresearch.runtime.promotion import (
    CHAMPION_PATH,
    adopt,
    champion_status,
    load,
    reconcile,
    stage_champion,
)
from autoresearch.softmax_tool.fake import FAULTS_ENV
from autoresearch.softmax_tool.submit import bootstrap_champion
from autoresearch.softmax_tool.upload import policy_name, upload_bootstrap
from softmax.test_softmax_cli import REPO, base_env, softmax

FAKE_BUILD = REPO / "games" / "fakegame" / "tools" / "fake_build.py"


def board_at(tmp_path, seed_champion=False) -> SubmitBoard:
    queue_dir = tmp_path / "canonical" / ".supervisor" / "runtime" / FILE_QUEUE_RELATIVE
    return SubmitBoard(tmp_path, queue_dir=queue_dir, seed_champion=seed_champion)


@pytest.fixture
def fresh(tmp_path):
    """A fresh install: nothing promoted, nothing on the fake leaderboard."""
    return board_at(tmp_path)


def fake_build(strategy_dir, out) -> None:
    subprocess.run(
        [sys.executable, str(FAKE_BUILD), str(strategy_dir), "--out", str(out), "--json"],
        check=True,
        capture_output=True,
    )


def started(board, mode="maintenance"):
    worker = board.admit(mode=mode)
    worker.start()
    return worker


def shipped_build(board, worker, name="champion"):
    """The fakegame starter policy build: the shipped STRATEGY of the worktree (GAME.md)."""
    out = board.tmp_path / name
    fake_build(worker.worktree / "strategy", out)
    return out


def uploads(board) -> dict:
    path = board.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    return json.loads(path.read_text())["uploads"] if path.exists() else {}


def submissions(board) -> list[dict]:
    return [s for s in board.sm.submissions(PLAYER) if s["league_id"] == LEAGUE]


def bootstrap_cli(board, worker, build, **extra):
    env = base_env(board, worker, **extra)
    return softmax(env, "bootstrap-champion", "--build-dir", str(build))


def cli(env, group, *argv):
    proc = subprocess.run(
        [sys.executable, "-m", "autoresearch", group, *argv],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.stdout.strip(), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def publish(board, worker) -> None:
    worker.write_journal()
    worker.finish()
    [handled] = board.process()
    assert handled.state == "published"


# --- the whole path ---


def test_fresh_install_bootstraps_the_champion_then_an_experiment_designs_against_it(fresh):
    _, board = softmax(base_env(fresh), "leaderboard")
    assert board["data"]["current_champion"] is None
    assert board["data"]["note"].startswith("no champion:")

    worker = started(fresh)
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 0, data
    champion = data["data"]["platform_policy_version_id"]
    version_id = data["data"]["version_id"]

    version = worker.store.load(version_id)[0]
    assert version["experiment"] is None and version["player_id"] == PLAYER
    assert version["policy_name"] == policy_name("bootstrap", version["artifact_sha256"])
    assert version["gates"] == {"local_check": {"passed": True}}
    assert worker.store.find("version_event", version=version_id, kind="uploaded")
    [submitted] = worker.store.find("version_event", version=version_id, kind="submitted")
    assert worker.store.load(submitted)[0]["data"]["promotion_id"] == data["data"]["promotion_id"]
    current = load(fresh.canonical)
    assert current.kind == "bootstrap" and current.experiment is None
    assert current.version == version_id and current.stage == "submitted"
    assert [s["policy_version_id"] for s in submissions(fresh)] == [champion]

    publish(fresh, worker)
    assert load(fresh.canonical).stage == "published"
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE) == (champion, "")

    # A second bootstrap is refused: the champion exists now.
    late = started(fresh)
    code, refused = bootstrap_cli(fresh, late, shipped_build(fresh, late, "late"))
    assert code == 1 and refused["error"]["reason"] == "champion_exists"

    # The next experiment designs and launches against that champion (A9).
    exp_worker = started(fresh, mode="experimentation")
    env = base_env(fresh, exp_worker)
    scratch = exp_worker.worktree / ".scratch"
    scratch.mkdir()
    _, game_version = softmax(env, "game-version")
    game_version = game_version["data"]["game_version"]
    rows = scratch / "league.jsonl"
    code, out = softmax(env, "league-episodes", "--limit", "60", "--out", str(rows))
    assert code == 0, out
    code, out = cli(
        env, "stats", "ingest", "--rows", str(rows), "--league", LEAGUE,
        "--game-version", game_version, "--game", "fakegame",
    )  # fmt: skip
    assert code == 0, out
    code, out = cli(
        env, "stats", "noise", "--league", LEAGUE, "--game-version", game_version,
        "--metrics", "score,errors",
    )  # fmt: skip
    assert code == 0, out
    _, board = softmax(env, "leaderboard")
    assert board["data"]["current_champion"] == champion
    meta = {
        "hypothesis": fresh.hypothesis,
        "mode": "experimentation",
        "lever": "patience",
        "baseline": champion,
        "change": {"components": ["patience"], "description": "More patience."},
        "environment": {"game_id": "fakegame", "game_version": game_version},
        "status": "designed",
        "xp_requests": [],
        "results": [],
        "lifecycle": {"launch_attempts": 0},
    }
    (scratch / "experiment.json").write_text(json.dumps(meta))
    (scratch / "experiment.md").write_text("Decision rule: promote when eligible.\n")
    code, out = cli(
        env, "records", "new", "experiment", "--meta-file", str(scratch / "experiment.json"),
        "--body-file", str(scratch / "experiment.md"), "--slug", "patience",
    )  # fmt: skip
    assert code == 0, out
    experiment = out["data"]["id"]
    request = {
        "baseline_version": champion,
        "seats": [0],
        "opponents": ["fake-house:v1"],
        "seeds": list(range(1, 21)),
        "primary_metrics": [{"name": "errors", "direction": "down", "min_effect": 2.0}],
        "guardrails": [],
    }
    (scratch / "design-request.json").write_text(json.dumps(request))
    code, out = cli(
        env, "stats", "design", experiment, "--request-file", str(scratch / "design-request.json")
    )
    assert code == 0, out
    candidate = exp_worker.worktree / "strategy" / "candidates" / experiment
    candidate.mkdir(parents=True)
    (candidate / "STRATEGY.md").write_text("# Strategy\n\n## Parameters\n\n- patience: 8\n")
    build = fresh.tmp_path / "candidate-build"
    fake_build(candidate, build)
    code, out = cli(env, "records", "status", experiment, "built")
    assert code == 0, out
    code, out = softmax(env, "upload", experiment, "--build-dir", str(build))
    assert code == 0, out
    code, out = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0, out
    baseline_arm = [r for r in out["data"]["created"] if r["arm"] == "baseline"]
    assert baseline_arm and {r["policy"] for r in baseline_arm} == {champion}


# --- refusals ---


def test_refused_when_a_leaderboard_champion_exists(tmp_path):
    board = board_at(tmp_path, seed_champion=True)  # A9: the leaderboard champion
    worker = started(board)
    code, data = bootstrap_cli(board, worker, shipped_build(board, worker))
    assert code == 1 and data["error"]["reason"] == "champion_exists"
    assert "autoresearch-fake:v0" in data["error"]["message"]
    assert uploads(board) == {} and worker.store.find("version") == []
    assert load(board.canonical) is None


def test_refused_unless_the_strategy_is_the_shipped_one(fresh):
    worker = started(fresh)
    strategy = worker.worktree / CHAMPION_PATH
    strategy.write_text(strategy.read_text() + "\n- aggression: 9\n")
    gitops.commit_all(worker.worktree, "edit the champion strategy")
    build = shipped_build(fresh, worker)
    code, data = bootstrap_cli(fresh, worker, build)
    assert code == 1 and data["error"]["reason"] == "not_shipped_strategy"
    assert uploads(fresh) == {} and load(fresh.canonical) is None  # refused before upload

    # The check after the lock reads the branch, whatever the caller checked before.
    version, platform_id = upload_bootstrap(worker.ctx(), fresh.sm, build, PLAYER)
    outcome = promotion.bootstrap_promotion(
        worker.ctx(), fresh.queue, fresh.sm, version, platform_id, LEAGUE
    )
    assert outcome.ok is False and outcome.code == "not_shipped_strategy"
    assert load(fresh.canonical).stage == "aborted" and submissions(fresh) == []


def test_refused_for_an_uncommitted_edit_built_from_the_worktree(fresh):
    worker = started(fresh)
    strategy = worker.worktree / CHAMPION_PATH
    strategy.write_text(strategy.read_text() + "\n- aggression: 9\n")  # not committed
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 1 and data["error"]["reason"] == "not_shipped_strategy"
    assert uploads(fresh) == {} and load(fresh.canonical) is None


def test_refused_when_the_build_compiled_another_strategy(fresh):
    worker = started(fresh)
    other = fresh.tmp_path / "other-strategy"
    other.mkdir()
    (other / "STRATEGY.md").write_text("# Not the shipped strategy\n")
    build = fresh.tmp_path / "other-build"
    fake_build(other, build)  # records the other STRATEGY.md's hash
    code, data = bootstrap_cli(fresh, worker, build)
    assert code == 1 and data["error"]["reason"] == "not_shipped_strategy"
    assert "sha256" in data["error"]["message"]
    assert uploads(fresh) == {} and load(fresh.canonical) is None


def test_post_lock_check_refuses_a_leaderboard_champion(tmp_path):
    board = board_at(tmp_path, seed_champion=True)
    worker = started(board)
    version, platform_id = upload_bootstrap(worker.ctx(), board.sm, shipped_build(board, worker))
    outcome = promotion.bootstrap_promotion(
        worker.ctx(), board.queue, board.sm, version, platform_id, LEAGUE
    )
    assert outcome.ok is False and outcome.code == "champion_exists"
    assert load(board.canonical).stage == "aborted"
    assert [s["policy_version_id"] for s in submissions(board)] == ["autoresearch-fake:v0"]


@pytest.mark.parametrize(
    "version_json",
    [
        {"gates": {"G1": {"passed": False}}},
        {"gates": {"G1": {"passed": True}}, "status": "errored"},
    ],
)
def test_failed_or_errored_build_is_refused(fresh, version_json):
    worker = started(fresh)
    build = fresh.tmp_path / "build"
    build.mkdir()
    (build / "p.json").write_text("{}")
    (build / "version.json").write_text(
        json.dumps({"artifact": "p.json", "strategy_sha256": "a" * 64, **version_json})
    )
    code, data = bootstrap_cli(fresh, worker, build)
    assert code == 1 and data["error"]["reason"] == "gates_failed"
    assert uploads(fresh) == {} and load(fresh.canonical) is None


def test_identity_fault_uploads_and_promotes_nothing(fresh):
    worker = started(fresh)
    code, data = bootstrap_cli(
        fresh, worker, shipped_build(fresh, worker), **{FAULTS_ENV: "player=ply_other"}
    )
    assert code == 1 and data["error"]["reason"] == "identity"
    assert uploads(fresh) == {} and worker.store.find("version") == []
    assert load(fresh.canonical) is None


# --- concurrency ---


def test_two_bootstrappers_only_one_promotes(fresh):
    first, second = started(fresh), started(fresh)
    first_build, second_build = shipped_build(fresh, first, "a"), shipped_build(fresh, second, "b")

    # Both pass the early checks and upload before either takes the promotion lock.
    first_version, first_pv = upload_bootstrap(first.ctx(), fresh.sm, first_build, PLAYER)
    second_version, second_pv = upload_bootstrap(second.ctx(), fresh.sm, second_build, PLAYER)

    won = promotion.bootstrap_promotion(
        first.ctx(), fresh.queue, fresh.sm, first_version, first_pv, LEAGUE
    )
    assert won.ok, won.reason
    lost = promotion.bootstrap_promotion(
        second.ctx(), fresh.queue, fresh.sm, second_version, second_pv, LEAGUE
    )
    assert lost.ok is False and "lock" in lost.reason
    assert [s["policy_version_id"] for s in submissions(fresh)] == [first_pv]

    # Through the CLI, the second card is refused before it uploads anything more.
    code, data = bootstrap_cli(fresh, second, second_build)
    assert code == 1 and data["error"]["reason"] == "promotion_in_flight"

    publish(fresh, first)
    late = promotion.bootstrap_promotion(
        second.ctx(), fresh.queue, fresh.sm, second_version, second_pv, LEAGUE
    )
    assert late.ok is False and "champion" in late.reason
    assert load(fresh.canonical).stage == "aborted"
    assert len(submissions(fresh)) == 1


# --- crashes and retries ---


def test_crash_before_the_upload_event_retries_on_the_same_version(fresh, monkeypatch):
    worker = started(fresh)
    build = shipped_build(fresh, worker)

    def broken(artifact, name):
        raise RuntimeError("platform down")

    monkeypatch.setattr(fresh.sm, "upload", broken)
    with pytest.raises(RuntimeError):
        bootstrap_champion(worker.ctx(), fresh.queue, fresh.sm, build, LEAGUE, PLAYER)
    [version] = worker.store.find("version")
    assert load(fresh.canonical) is None
    monkeypatch.undo()

    data = bootstrap_champion(worker.ctx(), fresh.queue, fresh.sm, build, LEAGUE, PLAYER)
    assert data["version_id"] == version
    assert list(uploads(fresh)) == [data["platform_policy_version_id"]]
    assert len(submissions(fresh)) == 1


def test_crash_after_the_upload_before_the_promotion_reuses_the_upload(fresh, monkeypatch):
    worker = started(fresh)
    build = shipped_build(fresh, worker)

    def crash(*args, **kwargs):
        raise RuntimeError("worker died")

    monkeypatch.setattr(promotion, "bootstrap_promotion", crash)
    with pytest.raises(RuntimeError):
        bootstrap_champion(worker.ctx(), fresh.queue, fresh.sm, build, LEAGUE, PLAYER)
    assert len(uploads(fresh)) == 1 and load(fresh.canonical) is None
    monkeypatch.undo()

    data = bootstrap_champion(worker.ctx(), fresh.queue, fresh.sm, build, LEAGUE, PLAYER)
    assert len(uploads(fresh)) == 1
    assert [s["policy_version_id"] for s in submissions(fresh)] == [
        data["platform_policy_version_id"]
    ]


# --- A11 recovery ---


def test_bootstrap_holder_died_after_submit_recovery_card_publishes(fresh):
    worker = started(fresh)
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 0, data
    worker.end_run("crashed")  # died before card finish
    pid = load(fresh.canonical).id

    [action] = reconcile(fresh.ctx(), fresh.queue, fresh.sm)
    assert action.kind == "recovery_requested"
    spec = fresh.follow_up(action.request_id)["spec"]
    assert spec["refs"] == [f"ar://version/{data['data']['version_id']}"]

    version_id = data["data"]["version_id"]
    assert "versions/" + version_id in spec["why"]  # how to restore the record
    # The version record and its upload event were committed on the holder's branch before
    # intent, so the pushed candidate commit carries them.
    sha = load(fresh.canonical).candidate_sha
    assert gitops.show(fresh.canonical, sha, f"versions/{version_id}/version.json")

    rescuer = fresh.admit_follow_up(action.request_id)
    rescuer.start()
    adopted = adopt(rescuer.ctx(), pid)
    stage_champion(rescuer.ctx(), adopted)  # also restores the version record
    assert (rescuer.worktree / f"versions/{version_id}/version.json").exists()
    publish(fresh, rescuer)

    canonical = RecordStore(Layout(fresh.canonical))
    assert canonical.load(version_id)[0]["policy_name"].startswith("ar-bootstrap-")
    assert canonical.find("version_event", version=version_id, kind="uploaded")
    resolve(fresh.canonical, f"ar://version/{version_id}")  # the refs resolve

    assert load(fresh.canonical).stage == "published"
    assert (fresh.canonical / CHAMPION_PATH).read_text() == "# Champion v0\n"
    assert len(submissions(fresh)) == 1
    champion = data["data"]["platform_policy_version_id"]
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE)[0] == champion

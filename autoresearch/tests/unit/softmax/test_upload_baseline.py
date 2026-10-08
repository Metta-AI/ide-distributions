"""First-baseline bootstrap (amendment A14): ``softmax upload-baseline`` and the baseline as the
comparison champion before any league entry.

A fresh install has no platform policy version, so no evaluation design can name a
``baseline_version`` and no experiment can be built. ``upload-baseline`` uploads a passed build
as the baseline without an experiment; experiments then cite its platform id.
"""

import json
import subprocess
import sys

import pytest
from support.promotion_board import LEAGUE, PLAYER, SubmitBoard

from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore
from autoresearch.runtime.promotion import champion_status
from autoresearch.softmax_tool import upload as upload_module
from autoresearch.softmax_tool.fake import FAULTS_ENV
from autoresearch.softmax_tool.protocol import Refused
from autoresearch.softmax_tool.upload import policy_name, upload_baseline
from autoresearch.softmax_tool.versions import BASELINE_ROLE, current_baseline
from softmax.test_softmax_cli import REPO, base_env, softmax

FAKE_BUILD = REPO / "games" / "fakegame" / "tools" / "fake_build.py"


@pytest.fixture
def fresh(tmp_path):
    """A fresh install: no promotion, nothing on the fake leaderboard, no version record."""
    return SubmitBoard(tmp_path, seed_champion=False)


def fake_build(strategy_dir, out) -> None:
    subprocess.run(
        [sys.executable, str(FAKE_BUILD), str(strategy_dir), "--out", str(out), "--json"],
        check=True,
        capture_output=True,
    )


def started(board, mode="experimentation"):
    worker = board.admit(mode=mode)
    worker.start()
    return worker


def baseline_build(board, worker, name="baseline"):
    """The fakegame baseline build: the champion STRATEGY of the worktree (GAME.md)."""
    out = board.tmp_path / name
    fake_build(worker.worktree / "strategy", out)
    return out


def uploads(board) -> dict:
    path = board.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    return json.loads(path.read_text())["uploads"] if path.exists() else {}


def test_upload_baseline_records_a_baseline_version_and_uploads_once(fresh):
    worker = started(fresh)
    build = baseline_build(fresh, worker)
    env = base_env(fresh, worker)

    code, data = softmax(env, "upload-baseline", "--build-dir", str(build))
    assert code == 0, data
    version_id = data["data"]["version_id"]
    platform_id = data["data"]["platform_policy_version_id"]
    assert data["data"]["role"] == BASELINE_ROLE

    version = worker.store.load(version_id)[0]
    assert version["role"] == BASELINE_ROLE and version["experiment"] is None
    assert version["player_id"] == PLAYER
    assert version["policy_name"] == policy_name("baseline", version["artifact_sha256"])
    assert version["gates"] == {"local_check": {"passed": True}}
    assert version["source_commit"] == gitops.head(worker.worktree)
    [event] = worker.store.find("version_event", version=version_id, kind="uploaded")
    assert worker.store.load(event)[0]["data"] == {"policy_version_id": platform_id}

    # Idempotent: the same build again returns the same version and uploads nothing.
    code, again = softmax(env, "upload-baseline", "--build-dir", str(build))
    assert code == 0, again
    assert again["data"]["version_id"] == version_id
    assert again["data"]["platform_policy_version_id"] == platform_id
    assert list(uploads(fresh)) == [platform_id]
    assert len(worker.store.find("version", role=BASELINE_ROLE)) == 1


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
    code, data = softmax(base_env(fresh, worker), "upload-baseline", "--build-dir", str(build))
    assert code == 1 and data["error"]["reason"] == "gates_failed"
    assert uploads(fresh) == {} and worker.store.find("version") == []


def test_identity_fault_uploads_nothing(fresh):
    worker = started(fresh)
    build = baseline_build(fresh, worker)
    env = base_env(fresh, worker, **{FAULTS_ENV: "player=ply_other"})
    code, data = softmax(env, "upload-baseline", "--build-dir", str(build))
    assert code == 1 and data["error"]["reason"] == "identity"
    assert uploads(fresh) == {} and worker.store.find("version") == []


def test_refused_when_a_leaderboard_champion_exists(tmp_path):
    board = SubmitBoard(tmp_path)  # A9: the seeded leaderboard champion is the champion
    worker = started(board)
    build = baseline_build(board, worker)
    code, data = softmax(base_env(board, worker), "upload-baseline", "--build-dir", str(build))
    assert code == 1 and data["error"]["reason"] == "champion_exists"
    assert "autoresearch-fake:v0" in data["error"]["message"]
    assert worker.store.find("version") == []


def test_refused_once_a_promotion_is_published(fresh, monkeypatch):
    worker = started(fresh)
    build = baseline_build(fresh, worker)
    monkeypatch.setattr(upload_module, "has_published", lambda canonical: True)
    with pytest.raises(Refused) as caught:
        upload_baseline(worker.ctx(), fresh.sm, build, LEAGUE, player_id=PLAYER)
    assert caught.value.reason == "champion_exists"
    assert worker.store.find("version") == []


def test_another_baseline_needs_supersedes(fresh):
    worker = started(fresh)
    env = base_env(fresh, worker)
    _, first = softmax(env, "upload-baseline", "--build-dir", str(baseline_build(fresh, worker)))
    first_id = first["data"]["version_id"]

    other = fresh.tmp_path / "other-strategy"
    other.mkdir()
    (other / "STRATEGY.md").write_text("# Strategy\n\n## Parameters\n\n- aggression: 4\n")
    second_build = fresh.tmp_path / "second"
    fake_build(other, second_build)

    code, data = softmax(env, "upload-baseline", "--build-dir", str(second_build))
    assert code == 1 and data["error"]["reason"] == "baseline_exists"
    assert first_id in data["error"]["message"]

    code, data = softmax(
        env, "upload-baseline", "--build-dir", str(second_build), "--supersedes", "V-WRONG"
    )
    assert code == 1 and data["error"]["reason"] == "bad_supersedes"

    code, data = softmax(
        env, "upload-baseline", "--build-dir", str(second_build), "--supersedes", first_id
    )
    assert code == 0, data
    second_id = data["data"]["version_id"]
    assert worker.store.load(second_id)[0]["supersedes"] == first_id
    assert current_baseline(worker.store).version_id == second_id
    assert len(uploads(fresh)) == 2


def merge_into_canonical(board, worker) -> None:
    """What the card's handoff merge does for these records (they are new files)."""
    gitops.commit_all(worker.worktree, "baseline")
    gitops.git(board.canonical, "merge", "--no-ff", "-m", "merge", f"card/{worker.request_id}")


def test_baseline_stands_in_as_champion_until_a_league_entry(fresh):
    worker = started(fresh)
    env = base_env(fresh, worker)
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE)[0] is None
    _, data = softmax(env, "upload-baseline", "--build-dir", str(baseline_build(fresh, worker)))
    platform_id = data["data"]["platform_policy_version_id"]
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE)[0] is None  # not merged yet

    merge_into_canonical(fresh, worker)
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE) == (platform_id, "")
    code, board = softmax(base_env(fresh), "leaderboard")
    assert code == 0, board
    assert board["data"]["leaderboard_champion"] is None
    assert board["data"]["current_champion"] == platform_id
    assert board["data"]["baseline"] == {
        "version_id": data["data"]["version_id"],
        "platform_policy_version_id": platform_id,
    }

    # A league entry (A9) takes over from the baseline.
    fresh.sm.submit("autoresearch-fake:v7", LEAGUE)
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE)[0] == "autoresearch-fake:v7"


def test_baseline_without_an_upload_is_no_champion(fresh):
    worker = started(fresh)
    worker.store.create(
        "version",
        {
            "role": BASELINE_ROLE,
            "parents": [],
            "strategy_sha256": "1" * 64,
            "source_commit": "2" * 40,
            "artifact_sha256": "3" * 64,
            "policy_name": "ar-baseline-333333333333",
            "experiment": None,
        },
    )
    merge_into_canonical(fresh, worker)
    assert current_baseline(RecordStore(Layout(fresh.canonical))).platform_policy_version_id is None
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE)[0] is None


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


def test_fresh_install_bootstraps_a_baseline_and_the_experiment_proceeds(fresh):
    """Fresh install -> no baseline -> upload-baseline -> design freezes -> build, upload and
    the paired XP launch, all in one experimentation card on the fake platform."""
    worker = started(fresh)
    env = base_env(fresh, worker)
    scratch = worker.worktree / ".scratch"
    scratch.mkdir()

    # Calibration (C10) needs no baseline: league episodes measure the noise.
    _, version = softmax(env, "game-version")
    game_version = version["data"]["game_version"]
    rows = scratch / "league.jsonl"
    code, data = softmax(env, "league-episodes", "--limit", "60", "--out", str(rows))
    assert code == 0, data
    code, data = cli(
        env, "stats", "ingest", "--rows", str(rows), "--league", LEAGUE,
        "--game-version", game_version, "--game", "fakegame",
    )  # fmt: skip
    assert code == 0, data
    code, data = cli(
        env, "stats", "noise", "--league", LEAGUE, "--game-version", game_version,
        "--metrics", "score,errors",
    )  # fmt: skip
    assert code == 0, data

    # No champion and no baseline: bootstrap the baseline in this card.
    _, board = softmax(env, "leaderboard")
    assert board["data"]["current_champion"] is None and board["data"]["baseline"] is None
    code, data = softmax(env, "upload-baseline", "--build-dir", str(baseline_build(fresh, worker)))
    assert code == 0, data
    baseline_pv = data["data"]["platform_policy_version_id"]
    baseline_id = data["data"]["version_id"]

    meta = {
        "hypothesis": fresh.hypothesis,
        "mode": "experimentation",
        "lever": "patience",
        "baseline": baseline_id,
        "change": {"components": ["patience"], "description": "More patience."},
        "environment": {"game_id": "fakegame", "game_version": game_version},
        "status": "designed",
        "xp_requests": [],
        "results": [],
        "lifecycle": {"launch_attempts": 0},
    }
    (scratch / "experiment.json").write_text(json.dumps(meta))
    (scratch / "experiment.md").write_text("Decision rule: promote when eligible.\n")
    code, data = cli(
        env, "records", "new", "experiment", "--meta-file", str(scratch / "experiment.json"),
        "--body-file", str(scratch / "experiment.md"), "--slug", "patience",
    )  # fmt: skip
    assert code == 0, data
    experiment = data["data"]["id"]

    request = {
        "baseline_version": baseline_pv,
        "seats": [0],
        "opponents": ["fake-house:v1"],
        "seeds": list(range(1, 21)),
        "primary_metrics": [{"name": "errors", "direction": "down", "min_effect": 2.0}],
        "guardrails": [],
    }
    (scratch / "design-request.json").write_text(json.dumps(request))
    code, data = cli(
        env, "stats", "design", experiment, "--request-file", str(scratch / "design-request.json")
    )
    assert code == 0, data
    assert data["data"]["evaluation_design"]["baseline_version"] == baseline_pv

    candidate = worker.worktree / "strategy" / "candidates" / experiment
    candidate.mkdir(parents=True)
    (candidate / "STRATEGY.md").write_text("# Strategy\n\n## Parameters\n\n- patience: 8\n")
    build = fresh.tmp_path / "candidate-build"
    fake_build(candidate, build)
    code, data = cli(env, "records", "status", experiment, "built")
    assert code == 0, data
    code, data = softmax(env, "upload", experiment, "--build-dir", str(build))
    assert code == 0, data
    code, data = softmax(env, "xp", "create", experiment, "--batch", "1", "--attempt", "1")
    assert code == 0, data
    baseline_requests = [r for r in data["data"]["created"] if r["arm"] == "baseline"]
    assert baseline_requests and {r["policy"] for r in baseline_requests} == {baseline_pv}
    assert worker.store.load(experiment)[0]["status"] == "evaluating"

"""First champion on a fresh install (amendment A14): ``softmax bootstrap-champion``.

With no champion (no published promotion and no leaderboard entry for our player, A9), a card
builds the game's starter policy, uploads it, and promotes it through the normal C8
promotion (acquire -> intent -> submit -> recorded -> published) as a ``bootstrap`` promotion:
no experiment, no evaluation outcome, no decision. Experiments then compare against it.
"""

import datetime
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
from autoresearch.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
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
        "opponent_count": 1,  # fakegame: two seats, one opponent (A12)
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


def test_a_lost_champion_is_not_a_fresh_install(fresh, capsys, monkeypatch):
    """A9 x A14: once a loop promotion reached the league, "no champion" means it was lost;
    a bootstrap is refused until a human accepts "no champion" (``accept-champion``)."""
    worker = started(fresh)
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 0, data
    publish(fresh, worker)
    state_path = fresh.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = json.loads(state_path.read_text())
    state["submissions"] = []  # the league dropped our champion
    state_path.write_text(json.dumps(state))

    again = started(fresh)
    code, refused = bootstrap_cli(fresh, again, shipped_build(fresh, again, "again"))
    assert code == 1 and refused["error"]["reason"] == "champion_conflict"
    assert "lost" in refused["error"]["message"]

    record = promotion.accept_champion(fresh.ctx(), fresh.sm, LEAGUE)
    assert record["policy_version_id"] is None
    assert promotion.bootstrap_blocker(fresh.canonical) is None


def test_a_rejected_starter_policy_waits_for_a_human(fresh):
    """N1: the league rejects the bootstrap: the promotion is settled ``rejected`` (the slot is
    free), one help_request tells the human what to run, and a new bootstrap waits for
    ``accept-champion``."""
    worker = started(fresh)
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 0, data
    starter = data["data"]["platform_policy_version_id"]
    worker.end_run("crashed")
    state_path = fresh.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = json.loads(state_path.read_text())
    state["memberships"] = {starter: {"stage": "rejected"}}
    state["submissions"] = []
    state_path.write_text(json.dumps(state))

    [action] = promotion.reconcile(fresh.ctx(), fresh.queue, fresh.sm, LEAGUE)
    assert action.kind == "rejected" and load(fresh.canonical).stage == "rejected"
    outbox = [
        json.loads(path.read_text())
        for path in sorted((fresh.canonical / "comms" / "outbox").glob("*.json"))
    ]
    [help_request] = [m for m in outbox if m["title"] == "The league rejected the first champion"]
    assert help_request["kind"] == "help_request"
    assert "promotion accept-champion" in help_request["body"]

    again = started(fresh)
    code, refused = bootstrap_cli(fresh, again, shipped_build(fresh, again, "again"))
    assert code == 1 and refused["error"]["reason"] == "champion_conflict"
    assert "rejected the starter policy" in refused["error"]["message"]

    record = promotion.accept_champion(fresh.ctx(), fresh.sm, LEAGUE)
    assert record["after_rejected"] == load(fresh.canonical).id
    code, data = bootstrap_cli(fresh, again, shipped_build(fresh, again, "again2"))
    assert code == 0, data  # the human let it try again


def test_refused_while_paused_for_a_champion_conflict(fresh):
    ledger = fresh.canonical / ".supervisor" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "pause.json").write_text(
        json.dumps({"reason": "champion conflict: test", "paused_at": "t", "tasks": {}})
    )
    worker = started(fresh)
    code, data = bootstrap_cli(fresh, worker, shipped_build(fresh, worker))
    assert code == 1 and data["error"]["reason"] == "champion_conflict"
    assert uploads(fresh) == {}


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


# --- the coach's IDE Controls (Softmax hosting) ---


def _coach_messages(board) -> list[dict]:
    outbox = sorted((board.canonical / "comms" / "outbox").glob("*.json"))
    keys = ("permit-needed:", "league-not-chosen:", "coach-declined:")
    messages = [json.loads(path.read_text()) for path in outbox]
    return [m for m in messages if str(m.get("runtime_key", "")).startswith(keys)]


def _minutes(moment: str, minutes: float) -> datetime.datetime:
    return datetime.datetime.fromisoformat(moment) + datetime.timedelta(minutes=minutes)


def _fake_state_path(board):
    return board.canonical / ".supervisor" / "runtime" / "fake_softmax.json"


def _answer(board, key: str, answer: str) -> None:
    state = json.loads(_fake_state_path(board).read_text())
    state.setdefault("answers", {})[key] = answer
    _fake_state_path(board).write_text(json.dumps(state))


def _refused_bootstrap(board, faults="permit=submit"):
    worker = started(board)
    build = shipped_build(board, worker)
    code, data = bootstrap_cli(board, worker, build, **{FAULTS_ENV: faults})
    return worker, code, data


def test_a_permit_refusal_leaves_the_bootstrap_waiting_and_a_later_run_submits_it(fresh):
    """The IDE Controls ask before league submissions: the submit is refused (403
    permit_required) after the upload and the intent. The promotion stays at ``intent``
    waiting for the coach; reconcile asks the coach once and reads the answer every run,
    retrying nothing while the ask waits; after the yes it submits the same policy version
    at once, without a second upload, and the holder's handoff that waited merges."""
    worker, code, data = _refused_bootstrap(fresh)
    assert code == 1 and data["error"]["reason"] == "permit_required", data
    waiting = load(fresh.canonical)
    assert waiting.id == data["error"]["details"]["promotion_id"]
    assert waiting.stage == "intent" and waiting.waiting == "permit"
    detail = waiting.waiting_detail
    assert detail["refusals"] == 1 and detail["ask"] and detail["league_id"] == LEAGUE
    assert detail["state"] == "waiting"
    assert "waiting: permit" in waiting.history[-1]["note"]
    assert len(uploads(fresh)) == 1 and submissions(fresh) == []

    # The card ends; its handoff waits while the promotion is at intent.
    worker.write_journal()
    worker.finish(outcome="partial")
    [handled] = fresh.process()
    assert handled.state == "validated"

    # Reconcile: one help request; while the ask waits, no retry, even past the backoff.
    refusing = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit"})
    early = _minutes(detail["next_retry_after"], -1)
    actions = reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=early)
    assert [a.kind for a in actions] == ["help_requested"]
    [asked] = _coach_messages(fresh)
    assert asked["runtime_key"].startswith(f"permit-needed:{waiting.id}:")
    assert "Approve it in the IDE Controls" in asked["body"]
    assert "remove “submit” from Ask before" in asked["body"]
    assert asked["about"]["asks"] == detail["ask"]
    late = _minutes(detail["next_retry_after"], 600)
    assert reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=late) == []
    assert load(fresh.canonical).waiting_detail == detail  # not even retried
    assert submissions(fresh) == []

    # The coach says yes: the next run submits at once (the fault stays: the yes grants it).
    _answer(fresh, detail["ask"], "yes")
    actions = reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=early)
    assert "submitted" in [a.kind for a in actions]
    submitted = load(fresh.canonical)
    assert submitted.stage == "submitted" and submitted.waiting is None
    assert [s["policy_version_id"] for s in submissions(fresh)] == [
        waiting.platform_policy_version_id
    ]
    assert len(uploads(fresh)) == 1  # never uploaded again

    [handled] = fresh.process()
    assert handled.state == "published"
    assert load(fresh.canonical).stage == "published"
    assert champion_status(fresh.ctx(), fresh.sm, LEAGUE) == (
        waiting.platform_policy_version_id,
        "",
    )
    assert len(_coach_messages(fresh)) == 1


def test_a_coach_no_drops_the_waiting_promotion_and_frees_the_slot(fresh):
    """C2: a no aborts the waiting intent (nothing was submitted), with one note; nothing is
    retried or asked again, and the slot is free for another promotion."""
    _, _, data = _refused_bootstrap(fresh)
    assert data["error"]["reason"] == "permit_required"
    waiting = load(fresh.canonical)
    _answer(fresh, waiting.waiting_detail["ask"], "no")
    refusing = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit"})
    now = _minutes(waiting.waiting_detail["next_retry_after"], 1)
    actions = reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=now)
    assert [a.kind for a in actions] == ["help_requested", "aborted"]
    dropped = load(fresh.canonical)
    assert dropped.stage == "aborted" and dropped.history[-1]["note"] == promotion.DECLINED_NOTE
    assert promotion.declined(dropped.to_dict())
    assert submissions(fresh) == []
    [_, note] = _coach_messages(fresh)
    assert note["kind"] == "note" and note["runtime_key"] == f"coach-declined:{waiting.id}"
    assert reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=now) == []
    state = json.loads(_fake_state_path(fresh).read_text())
    assert list(state["asks"].values()) == [waiting.waiting_detail["ask"]]  # no new ask


def test_a_retry_that_raises_waits_for_the_next_backoff_step(fresh, monkeypatch):
    """I1: when the answer cannot be read the backoff decides; a retried submit that raises
    is recorded and moves the retry time on, so it is not repeated every minute."""
    _refused_bootstrap(fresh, "permit=submit;asks_read=fail")
    waiting = load(fresh.canonical)
    unreadable = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit;asks_read=fail"})
    calls = []

    def broken_submit(policy_version_id, league_id):
        calls.append(policy_version_id)
        raise RuntimeError("Request failed (409): league is locked to new policy submissions")

    monkeypatch.setattr(unreadable, "submit", broken_submit)
    now = _minutes(waiting.waiting_detail["next_retry_after"], 1)
    with pytest.raises(RuntimeError, match="locked"):
        reconcile(fresh.ctx(), fresh.queue, unreadable, LEAGUE, now=now)
    after = load(fresh.canonical).waiting_detail
    assert "locked" in after["last_error"] and after["refusals"] == 2
    assert datetime.datetime.fromisoformat(after["next_retry_after"]) > now
    assert reconcile(fresh.ctx(), fresh.queue, unreadable, LEAGUE, now=now) == []
    assert len(calls) == 1  # the next minute's run did not submit again


def test_a_league_choice_refusal_of_the_bootstrap_asks_the_coach_to_choose(fresh):
    _, code, data = _refused_bootstrap(fresh, "league=none")
    assert code == 1 and data["error"]["reason"] == "league_not_chosen", data
    waiting = load(fresh.canonical)
    assert waiting.stage == "intent" and waiting.waiting == "league"

    refusing = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "league=none"})
    early = _minutes(waiting.waiting_detail["next_retry_after"], -1)
    assert [a.kind for a in reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=early)] == [
        "help_requested"
    ]
    [asked] = _coach_messages(fresh)
    assert asked["runtime_key"] == f"league-not-chosen:{LEAGUE}:none"
    assert asked["title"] == "Choose the agent's league in the IDE Controls"
    assert f"choose the {LEAGUE} league for the agent in the IDE Controls" in asked["body"]
    assert "first 30 minutes after the refusal" in asked["body"]


def test_a_declined_bootstrap_blocks_new_bootstraps_until_accept_champion(fresh):
    """N1: after the coach's no there is still no champion; a new bootstrap would upload again
    and ask the coach again, so it waits for ``promotion accept-champion``, as after a
    rejected one."""
    _refused_bootstrap(fresh)
    waiting = load(fresh.canonical)
    _answer(fresh, waiting.waiting_detail["ask"], "no")
    refusing = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit"})
    now = _minutes(waiting.waiting_detail["next_retry_after"], 1)
    reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=now)
    [_, note] = _coach_messages(fresh)
    assert note["title"] == "No first champion: you declined the starter policy's submission"
    assert "research is paused" in note["body"] and "promotion accept-champion" in note["body"]
    assert "coach declined" in promotion.bootstrap_blocker(fresh.canonical)

    again = started(fresh)
    code, refused = bootstrap_cli(fresh, again, shipped_build(fresh, again, "again"))
    assert code == 1 and refused["error"]["reason"] == "champion_conflict"
    assert len(uploads(fresh)) == 1  # nothing uploaded, nothing asked
    record = promotion.accept_champion(fresh.ctx(), fresh.sm, LEAGUE)
    assert record["after_rejected"] == waiting.id
    assert promotion.bootstrap_blocker(fresh.canonical) is None


def test_a_retry_after_a_yes_that_raises_waits_for_the_next_backoff_step(fresh, monkeypatch):
    """N2: after the yes, a submit that raises is not repeated every minute."""
    _refused_bootstrap(fresh)
    waiting = load(fresh.canonical)
    _answer(fresh, waiting.waiting_detail["ask"], "yes")
    approved = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit"})
    calls = []

    def broken_submit(policy_version_id, league_id):
        calls.append(policy_version_id)
        raise RuntimeError("Request failed (409): league is locked to new policy submissions")

    monkeypatch.setattr(approved, "submit", broken_submit)
    now = datetime.datetime.now(datetime.UTC)
    with pytest.raises(RuntimeError, match="locked"):
        reconcile(fresh.ctx(), fresh.queue, approved, LEAGUE, now=now)
    for minute in (1, 2, 3):
        later = now + datetime.timedelta(minutes=minute)
        assert reconcile(fresh.ctx(), fresh.queue, approved, LEAGUE, now=later) == []
    assert len(calls) == 1
    due = datetime.datetime.fromisoformat(load(fresh.canonical).waiting_detail["next_retry_after"])
    after_due = due + datetime.timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="locked"):
        reconcile(fresh.ctx(), fresh.queue, approved, LEAGUE, now=after_due)
    assert len(calls) == 2


def test_a_waiting_submission_gets_one_reminder_after_a_day(fresh):
    """N6: one reminder about the same ask a day after it was first asked, never more."""
    _refused_bootstrap(fresh)
    waiting = load(fresh.canonical)
    since = datetime.datetime.fromisoformat(waiting.waiting_detail["since"])
    refusing = FakeSoftmax(fresh.canonical, {FAULTS_ENV: "permit=submit"})
    reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=since)
    day = since + datetime.timedelta(hours=24, minutes=1)
    actions = reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=day)
    assert [a.kind for a in actions] == ["help_requested"]
    week = since + datetime.timedelta(days=7)
    assert reconcile(fresh.ctx(), fresh.queue, refusing, LEAGUE, now=week) == []
    _, reminder = _coach_messages(fresh)
    assert reminder["title"] == "Still waiting for your approval in the IDE Controls"
    assert load(fresh.canonical).stage == "intent"  # no timeout

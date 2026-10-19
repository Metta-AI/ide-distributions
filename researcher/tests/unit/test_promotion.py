"""Promotion: the orchestrator's guarded submit, write-ahead stages, recovery and conflicts.

Runs against a research board (a bare canonical repository and a clone, which here stands for
the orchestrator's) and the FakeSoftmax, whose league admits a submission at once.
"""

import json
import os
import subprocess
import sys

import pytest
from support.research_board import BASELINE_PV, LEAGUE, PLAYER, PV_A, ResearchBoard, fake_state

from ide_research_core import promotion
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_json
from ide_research_core.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
from ide_research_core.softmax_tool.protocol import Refused


@pytest.fixture
def board(tmp_path):
    return ResearchBoard(tmp_path)


def published_on_canonical(board) -> list[str]:
    files = gitops.git(board.origin, "ls-tree", "-r", "--name-only", "main", "promotions/")
    return [json.loads(gitops.git(board.origin, "show", f"main:{f}"))["stage"] for f in files.split()]


def test_submit_records_intent_then_submitted_and_status_publishes_the_champion(board):
    experiment, version = board.reviewed_experiment()

    result = promotion.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)

    assert result["stage"] == "submitted" and result["submission_id"]
    stages = [entry["stage"] for entry in result["history"]]
    assert stages == ["intent", "submitted"]  # intent was published before the platform call
    assert board.store.find("version_event", version=version, kind="submitted")
    report = promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert report["in_flight"] is None and report["conflict"] is None
    assert report["champion"]["platform_policy_version_id"] == PV_A
    champion = gitops.git(board.origin, "show", f"main:{promotion.CHAMPION_PATH}")
    assert champion == f"# Candidate for {experiment}"
    assert "published" in published_on_canonical(board)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"decision": "hold"}, "not_promote"),
        ({"outcome": {"promote_eligible": False, "failures": ["ci"]}}, "not_eligible"),
        ({"outcome": {"rows_sha256": "0" * 64}}, "stale_outcome"),
        ({"outcome": {"champion": "someone-else:v1"}}, "baseline_not_champion"),
    ],
)
def test_guards_refuse_before_anything_is_recorded(board, kwargs, reason):
    experiment, _ = board.reviewed_experiment(**kwargs)
    with pytest.raises(Refused) as refused:
        promotion.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == reason
    assert promotion.in_flight(board.canonical) is None
    assert board.sm.submissions(PLAYER)[-1]["policy_version_id"] == BASELINE_PV


def test_a_wrong_identity_is_refused(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "player=ply_other"})
    with pytest.raises(Refused) as refused:
        promotion.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == "identity"


def test_one_promotion_at_a_time(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        promotion.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    second, _ = board.reviewed_experiment()
    with pytest.raises(Refused) as refused:
        promotion.submit(board.canonical, board.sm, second, LEAGUE, PLAYER)
    assert refused.value.reason == "promotion_in_flight"


def test_a_permit_ask_waits_at_intent_and_status_sends_after_the_yes(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused) as refused:
        promotion.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == "permit_required"
    waiting = promotion.in_flight(board.canonical)
    assert waiting.stage == "intent" and waiting.waiting["ask"]

    promotion.status(board.canonical, sm, LEAGUE, PLAYER)  # no answer yet: nothing is sent
    assert promotion.in_flight(board.canonical).stage == "intent"

    state = fake_state(board)
    state["answers"] = {waiting.waiting["ask"]: "yes"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
    report = promotion.status(board.canonical, sm, LEAGUE, PLAYER)  # one step: sent
    assert report["in_flight"]["stage"] == "submitted"
    report = promotion.status(board.canonical, sm, LEAGUE, PLAYER)  # admitted: published
    assert report["in_flight"] is None
    assert report["champion"]["platform_policy_version_id"] == PV_A


def test_a_declined_permit_aborts_the_promotion(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        promotion.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    ask = promotion.in_flight(board.canonical).waiting["ask"]
    state = fake_state(board)
    state["answers"] = {ask: "no"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)

    promotion.status(board.canonical, sm, LEAGUE, PLAYER)

    assert promotion.in_flight(board.canonical) is None
    [record] = [p for p in promotion.load_all(board.canonical) if p.kind == "experiment"]
    assert record.stage == "aborted"


def interrupted_intent(board, platform_id: str) -> promotion.Promotion:
    """An intent published by a run that died before it recorded the platform's answer."""
    record = promotion.Promotion(
        "P-01K0000000000000000000000Z", "experiment", "V-1", platform_id, LEAGUE, "new",
        experiment="E-1",
    )
    promotion._move(board.canonical, record, "intent", "submit E-1")
    return record


def test_an_interrupted_intent_is_aborted_when_the_platform_has_no_submission(board):
    interrupted_intent(board, PV_A)
    promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)
    [record] = [p for p in promotion.load_all(board.canonical) if p.kind == "experiment"]
    assert record.stage == "aborted"
    assert len(board.sm.submissions(PLAYER)) == 1  # never sent blindly


def test_an_interrupted_intent_the_platform_has_becomes_submitted(board):
    interrupted_intent(board, PV_A)
    board.sm.submit(PV_A, LEAGUE)  # the submit reached the platform before the crash
    promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)
    [record] = [p for p in promotion.load_all(board.canonical) if p.kind == "experiment"]
    assert record.submission_id and record.stage in ("submitted", "recorded")


def test_a_hand_submission_is_a_conflict_until_accepted(board):
    board.sm.submit("someone-else:v9", LEAGUE)
    report = promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert "someone-else:v9" in report["conflict"]

    promotion.accept(board.canonical, board.sm, LEAGUE, PLAYER)

    assert promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)["conflict"] is None


def test_bootstrap_needs_a_fresh_install(board):
    version = board.store.create("version", {"parents": [], "strategy_sha256": "1" * 64,
                                             "source_commit": "2" * 40,
                                             "artifact_sha256": "3" * 64,
                                             "policy_name": "bootstrap-x"})  # fmt: skip
    with pytest.raises(Refused) as refused:
        promotion.bootstrap(board.canonical, board.sm, version, LEAGUE, PLAYER)
    assert refused.value.reason == "champion_exists"


def test_bootstrap_refuses_a_changed_strategy(tmp_path):
    board = ResearchBoard(tmp_path, seed_champion=False)
    meta = {
        "parents": [], "strategy_sha256": "9" * 64, "source_commit": "2" * 40,
        "artifact_sha256": "3" * 64, "policy_name": "bootstrap-x",
        "build": {"source_path": "strategy/STRATEGY.md"},
    }
    version = board.store.create("version", meta)
    board.store.add_event(version, "uploaded", {"policy_version_id": "pv_x"})
    gitops.commit_all(board.canonical, "version")
    gitops.git(board.canonical, "push", "-q", "origin", "main")
    with pytest.raises(Refused) as refused:
        promotion.bootstrap(board.canonical, board.sm, version, LEAGUE, PLAYER)
    assert refused.value.reason == "not_shipped_strategy"


def test_the_cli_prints_the_envelope(board):
    experiment, _ = board.reviewed_experiment()
    env = {
        **os.environ,
        "HERMES_HOME": str(board.tmp_path / "home"),
        "RESEARCH_WORKSPACE": str(board.canonical),
        "RESEARCH_GAME": "fakegame",
        "RESEARCH_SOFTMAX": "fake",
        "RESEARCH_PLAYER_ID": PLAYER,
    }
    run = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "promotion", "submit", experiment],
        capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["data"]["stage"] == "submitted"


def test_a_send_that_dies_after_an_approved_permit_is_recovered_not_asked_again(board):
    """The coach said yes, the platform took the submission, the process died before saving
    ``submitted``: the next status finds the submission instead of asking again (R12 #4)."""
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        promotion.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    state = fake_state(board)
    state["answers"] = {promotion.in_flight(board.canonical).waiting["ask"]: "yes"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
    real_submit = sm.submit

    def accepted_then_died(*args):
        real_submit(*args)
        raise RuntimeError("died after the platform accepted")

    sm.submit = accepted_then_died
    with pytest.raises(RuntimeError):
        promotion.status(board.canonical, sm, LEAGUE, PLAYER)
    sm.submit = real_submit

    report = promotion.status(board.canonical, sm, LEAGUE, PLAYER)

    assert report["in_flight"]["stage"] == "submitted" and report["in_flight"]["waiting"] is None
    assert len([s for s in sm.submissions(PLAYER) if s["policy_version_id"] == PV_A]) == 1


def test_status_waits_for_a_live_submit_instead_of_aborting_it(board):
    """The supervisor's status and a chat turn's submit overlap (R12 #3)."""
    import threading

    experiment, _ = board.reviewed_experiment()
    sending, release = threading.Event(), threading.Event()
    real_submit = board.sm.submit

    def slow_submit(*args):
        sending.set()
        release.wait(10)
        return real_submit(*args)

    board.sm.submit = slow_submit
    submitter = threading.Thread(
        target=promotion.submit, args=(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    )
    submitter.start()
    assert sending.wait(10)
    checker = threading.Thread(
        target=promotion.status, args=(board.canonical, board.sm, LEAGUE, PLAYER)
    )
    checker.start()
    checker.join(1)
    assert checker.is_alive()  # waiting for the promotion lock
    release.set()
    submitter.join(10)
    checker.join(10)

    [record] = [p for p in promotion.load_all(board.canonical) if p.kind == "experiment"]
    assert "aborted" not in [entry["stage"] for entry in record.history]


def test_a_researchers_stale_clone_sees_the_published_champion(board, tmp_path):
    """A researcher's clone moves only when it integrates; the conflict check reads canonical
    ``main`` (R12 #2)."""
    researcher = tmp_path / "researcher"
    gitops.git(tmp_path, "clone", "-q", str(board.origin), str(researcher))
    experiment, _ = board.reviewed_experiment()
    promotion.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)

    assert promotion.baseline_problem(researcher, board.sm, LEAGUE, PLAYER, PV_A) is None


def test_a_rejected_champion_publish_is_redone_with_the_strategy(board, monkeypatch):
    """The push of ``published`` and the champion strategy was rejected; the retry must still
    publish the strategy, though the clone already holds it (Codex R14)."""
    from ide_research_core import workspace

    experiment, _ = board.reviewed_experiment()
    promotion.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    real_publish = workspace.publish

    def reject_published(clone, files, message):
        if message.endswith(": published"):
            monkeypatch.setattr(gitops, "push_with_rejections", lambda *_: (False, ["main"]))
        return real_publish(clone, files, message)

    monkeypatch.setattr(workspace, "publish", reject_published)
    with pytest.raises(workspace.WorkspaceError):
        promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)
    monkeypatch.undo()

    report = promotion.status(board.canonical, board.sm, LEAGUE, PLAYER)

    assert report["in_flight"] is None
    champion = gitops.show(board.origin, "main", promotion.CHAMPION_PATH)
    assert champion.strip() == f"# Candidate for {experiment}".encode()


# --- release: a core-loop program, no experiment ---


@pytest.fixture
def core_loop(monkeypatch):
    monkeypatch.setenv("SOFTMAX_COGENT_MODE", "core-loop")


@pytest.mark.parametrize("mode", [None, "", "research"])
def test_release_is_refused_outside_core_loop_mode(board, monkeypatch, mode):
    monkeypatch.delenv("SOFTMAX_COGENT_MODE", raising=False)
    if mode is not None:
        monkeypatch.setenv("SOFTMAX_COGENT_MODE", mode)
    with pytest.raises(Refused) as refused:
        promotion.release(board.sm, PV_A, LEAGUE, PLAYER)
    assert refused.value.reason == "not_core_loop"
    assert board.sm.submissions(PLAYER) == []


def test_the_release_cli_is_refused_outside_core_loop_mode(board):
    env = {k: v for k, v in os.environ.items() if k != "SOFTMAX_COGENT_MODE"} | {
        "HERMES_HOME": str(board.tmp_path / "home"),
        "RESEARCH_WORKSPACE": str(board.canonical),
        "RESEARCH_GAME": "fakegame",
        "RESEARCH_SOFTMAX": "fake",
        "RESEARCH_PLAYER_ID": PLAYER,
    }
    out = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "promotion", "release", "--policy-version", PV_A],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert json.loads(out.stdout)["error"]["reason"] == "not_core_loop"


def test_release_submits_the_uploaded_program_without_an_experiment(board, core_loop):
    before = promotion.load_all(board.canonical)
    result = promotion.release(board.sm, PV_A, LEAGUE, PLAYER)
    assert result["released"] is True and result["submission_id"]
    assert promotion.load_all(board.canonical) == before  # nothing recorded, no experiment
    again = promotion.release(board.sm, PV_A, LEAGUE, PLAYER)  # safe to ask again
    assert again["released"] is True and again["submission_id"] == result["submission_id"]
    assert [r["policy_version_id"] for r in board.sm.submissions(PLAYER)].count(PV_A) == 1


def test_release_waits_for_the_coachs_yes_then_sends(board, core_loop):
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    waiting = promotion.release(sm, PV_A, LEAGUE, PLAYER)
    assert waiting["released"] is False and waiting["reason"] == "permit_required"
    assert waiting["state"] == "waiting" and waiting["ask"]
    state = fake_state(board)
    state["answers"] = {waiting["ask"]: "yes"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
    sent = promotion.release(sm, PV_A, LEAGUE, PLAYER)
    assert sent["released"] is True and sent["submission_id"]


def test_release_reports_a_league_not_chosen(board, core_loop):
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "league=none"})
    result = promotion.release(sm, PV_A, LEAGUE, PLAYER)
    assert result["released"] is False and result["reason"] == "league_not_chosen"


def test_release_refuses_a_wrong_identity(board, core_loop):
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "player=ply_other"})
    with pytest.raises(Refused) as refused:
        promotion.release(sm, PV_A, LEAGUE, PLAYER)
    assert refused.value.reason == "identity"


def test_the_release_cli_prints_the_envelope(board, core_loop):
    env = {
        **os.environ,
        "HERMES_HOME": str(board.tmp_path / "home"),
        "RESEARCH_WORKSPACE": str(board.canonical),
        "RESEARCH_GAME": "fakegame",
        "RESEARCH_SOFTMAX": "fake",
        "RESEARCH_PLAYER_ID": PLAYER,
    }
    run = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "promotion", "release",
         "--policy-version", PV_A],
        capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["data"]["released"] is True

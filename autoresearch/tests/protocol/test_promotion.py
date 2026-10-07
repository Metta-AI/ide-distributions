"""Promotion state machine (Task 3.6, Overview C8): real git with a bare ``origin``, FileQueue,
FakeSoftmax and simulated workers. The Board/Worker helpers come from ``test_merge.py``."""

import datetime
import stat
from dataclasses import replace

import pytest
from support.board import Board, Worker

from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.runtime import promotion
from autoresearch.runtime.ledger import canonical_lock, commit
from autoresearch.runtime.promotion import (
    CHAMPION_PATH,
    PromotionError,
    acquire,
    adopt,
    current_champion,
    load,
    reconcile,
    record_intent,
    stage_champion,
    submit_promotion,
)
from autoresearch.softmax_tool.fake import FAULTS_ENV, FakeSoftmax

LEAGUE = "league_fake"
BASELINE_PV = "pv_base"  # the champion on the leaderboard before autoresearch promoted anything
CHAMPION_V0 = "# Champion v0\n"
VERSION = {
    "parents": [],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-fake",
}


class PromotionBoard(Board):
    """A Board with a bare ``origin``, a seeded champion STRATEGY and a FakeSoftmax whose
    leaderboard champion is ``BASELINE_PV``."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.origin = tmp_path / "origin.git"
        gitops.git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        gitops.git(self.canonical, "remote", "add", "origin", str(self.origin))
        (self.canonical / CHAMPION_PATH).write_text(CHAMPION_V0)
        gitops.commit_all(self.canonical, "seed champion")
        assert gitops.push(self.canonical, "origin", ["HEAD:refs/heads/main"])
        self.sm = FakeSoftmax(self.canonical, {})
        self.sm.submit(BASELINE_PV, LEAGUE)

    def new_experiment(self, platform_id: str, baseline: str = BASELINE_PV) -> tuple[str, str]:
        """Commit a version and an uploaded experiment bound to ``platform_id``."""
        version = self.store.create("version", dict(VERSION))
        experiment = self.store.create(
            "experiment",
            {
                "hypothesis": self.hypothesis,
                "parents": [],
                "mode": "experimentation",
                "lever": "SK.last_hit",
                "baseline": "V-0",
                "change": {"components": ["SK.last_hit"], "description": "Wait for range."},
                "evaluation_design": {"n_min": 10, "baseline_version": baseline},
                "environment": {"game_id": "fakegame", "game_version": "65"},
                "status": "designed",
            },
        )
        self.store.set_status(experiment, "built")
        self.store.set_status(experiment, "uploaded")
        binding = {"version_id": version, "platform_policy_version_id": platform_id}
        self.store.update(experiment, {"candidate_binding": binding})
        gitops.commit_all(self.canonical, f"experiment {experiment}")
        return experiment, version

    def submissions_of(self, platform_id: str) -> list[dict]:
        player = self.sm.whoami().player_id
        return [s for s in self.sm.submissions(player) if s["policy_version_id"] == platform_id]

    def promotion(self) -> promotion.Promotion | None:
        return load(self.canonical)

    def history(self, promotion_id: str) -> dict:
        return self.committed(self.ledger / "promotions" / f"{promotion_id}.json")


class Promoter:
    """A promotion card: a worker that commits a candidate STRATEGY on its branch, then
    promotes it."""

    def __init__(self, board: PromotionBoard, platform_id: str, baseline: str = BASELINE_PV):
        self.board = board
        self.platform_id = platform_id
        self.experiment, self.version = board.new_experiment(platform_id, baseline)
        self.worker: Worker = board.admit(
            refs=[f"ar://experiment/{self.experiment}"], mode="maintenance"
        )
        self.worker.start()
        self.candidate_path = f"strategy/candidates/{self.experiment}/STRATEGY.md"
        self.candidate_text = f"# Candidate {platform_id}\n"
        path = self.worker.worktree / self.candidate_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.candidate_text)
        gitops.commit_all(self.worker.worktree, f"candidate {self.experiment}")

    def ctx(self):
        return self.worker.ctx()

    def acquire(self) -> bool:
        return acquire(
            self.ctx(), self.board.queue, self.experiment, self.version, self.candidate_path
        )

    def submit(self, sm=None) -> promotion.SubmitOutcome:
        return submit_promotion(
            self.ctx(),
            self.board.queue,
            sm or self.board.sm,
            self.experiment,
            self.version,
            self.candidate_path,
            LEAGUE,
        )

    def finish(self) -> dict:
        self.worker.write_journal()
        return self.worker.finish()


@pytest.fixture
def board(tmp_path) -> PromotionBoard:
    return PromotionBoard(tmp_path)


def _stages(history: dict) -> list[str]:
    return [entry["stage"] for entry in history["history"]]


def _force(board: PromotionBoard, **fields) -> None:
    """Rewrite the current promotion's committed record (a state the test needs directly)."""
    current = replace(board.promotion(), **fields)
    data = current.to_dict()
    paths = [
        promotion.promotion_path(board.canonical),
        promotion.history_path(board.canonical, current.id),
    ]
    for path in paths:
        write_json(path, data)
    with canonical_lock(board.canonical):
        commit(board.canonical, paths, "test: force promotion")


def _minutes_ago(minutes: int) -> str:
    moment = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)
    return moment.isoformat(timespec="seconds")


def _clean(board: PromotionBoard) -> None:
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


# --- acquire ---


@pytest.mark.parametrize("stage", ["intent", "submitted", "recorded"])
def test_acquire_refused_while_in_flight_even_after_holder_ended(board, stage):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    a.worker.end_run("done")
    _force(board, stage=stage, updated_at=_minutes_ago(20))

    b = Promoter(board, "pv_b")
    assert b.acquire() is False
    assert board.promotion().holder_task == a.worker.task_id
    assert board.promotion().stage == stage


@pytest.mark.parametrize("stage", ["published", "aborted"])
def test_acquire_allowed_after_published_or_aborted(board, stage):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    _force(board, stage=stage)  # holder run still open: the stage alone frees the lock

    b = Promoter(board, "pv_b")
    assert b.acquire()
    current = board.promotion()
    assert current.holder_task == b.worker.task_id and current.stage == "acquired"
    assert current.platform_policy_version_id == "pv_b"
    assert current.id.startswith("P-")


def test_acquire_takes_over_only_an_abandoned_acquired_promotion(board):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    assert a.acquire()  # idempotent for its own holder
    first = board.promotion()
    b = Promoter(board, "pv_b")
    assert b.acquire() is False  # holder run still open

    a.worker.end_run("done")  # abandoned in `acquired`: no submit intent
    assert b.acquire()
    assert board.promotion().holder_task == b.worker.task_id
    assert board.history(first.id)["stage"] == "aborted"
    _clean(board)


# --- record_intent refusals ---


def test_record_intent_refuses_when_candidate_branch_push_fails(board):
    a = Promoter(board, "pv_a")
    gitops.git(board.canonical, "remote", "set-url", "origin", str(board.tmp_path / "missing"))
    assert a.acquire()

    with pytest.raises(PromotionError, match="candidate branch"):
        record_intent(a.ctx(), board.promotion())
    assert board.promotion().stage == "acquired"

    outcome = a.submit()
    assert outcome.ok is False and "candidate branch" in outcome.reason
    assert board.promotion().stage == "acquired"
    assert board.submissions_of("pv_a") == []
    _clean(board)


def test_record_intent_refuses_when_ledger_push_fails(board):
    hook = board.origin / "hooks" / "update"
    hook.write_text('#!/bin/sh\n[ "$1" = "refs/heads/main" ] && exit 1\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    a = Promoter(board, "pv_a")
    assert a.acquire()

    with pytest.raises(PromotionError, match="ledger"):
        record_intent(a.ctx(), board.promotion())

    current = board.promotion()
    assert current.stage == "acquired" and current.candidate_sha is None
    assert _stages(board.history(current.id)) == ["acquired", "intent", "acquired"]
    branch = f"refs/heads/card/{a.worker.request_id}"
    assert gitops.git(board.origin, "rev-parse", branch)  # the branch push itself worked
    assert a.submit().ok is False
    assert board.submissions_of("pv_a") == []
    _clean(board)


# --- the normal path ---


def test_promotion_merges_records_publishes_and_frees_the_lock(board):
    a = Promoter(board, "pv_a")
    outcome = a.submit()
    assert outcome.ok, outcome.reason
    current = board.promotion()
    assert current.stage == "submitted" and current.submission_id == outcome.submission_id
    assert gitops.show(board.origin, "main", promotion.PROMOTION_FILE_REL) is not None
    # submit_promotion staged the champion in the worker's worktree
    assert (a.worker.worktree / CHAMPION_PATH).read_text() == a.candidate_text
    a.finish()

    [handled] = board.process()

    assert handled.state == "published"
    history = board.history(current.id)
    assert _stages(history) == ["acquired", "intent", "submitted", "recorded", "published"]
    assert history["merge_sha"] == handled.merge_sha
    assert (board.canonical / CHAMPION_PATH).read_text() == a.candidate_text
    assert current_champion(board.ctx(), board.sm, LEAGUE) == "pv_a"
    assert len(board.submissions_of("pv_a")) == 1
    assert reconcile(board.ctx(), board.queue, board.sm) == []

    b = Promoter(board, "pv_b", baseline="pv_a")
    assert b.acquire()  # on_published freed the lock
    _clean(board)


def test_champion_not_baseline_refuses_and_requests_reevaluation(board):
    a = Promoter(board, "pv_a", baseline="pv_old")

    outcome = a.submit()

    assert outcome.ok is False and "pv_old" in outcome.reason and BASELINE_PV in outcome.reason
    assert board.submissions_of("pv_a") == []
    request = board.follow_up(outcome.request_id)
    spec = request["spec"]
    assert spec["refs"] == [f"ar://experiment/{a.experiment}", f"ar://version/{a.version}"]
    assert spec["created_by"] == "runtime" and spec["mode"] == "experimentation"
    assert board.promotion().stage == "aborted"
    # the experiment can promote again later; a second refusal reuses the same request
    again = a.submit()
    assert again.ok is False and again.request_id == outcome.request_id
    _clean(board)


def test_holder_branch_without_the_champion_goes_to_repair(board):
    a = Promoter(board, "pv_a")
    assert a.submit().ok
    (a.worker.worktree / CHAMPION_PATH).write_text("# something else\n")
    a.finish()

    [handled] = board.process()

    assert handled.state == "repair"
    assert "champion" in board.follow_up(handled.follow_up_request)["spec"]["why"]
    assert board.promotion().stage == "submitted"
    assert (board.canonical / CHAMPION_PATH).read_text() == CHAMPION_V0
    # the holder's handoff ended in repair, so reconcile asks for a promotion-recovery card
    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "recovery_requested"


# --- crash recovery ---


def _recover(board: PromotionBoard, request_id: str) -> Worker:
    """Run the promotion-recovery card as its worker would."""
    worker = board.admit_follow_up(request_id)
    worker.start()
    adopted = adopt(worker.ctx(), board.promotion().id)
    assert adopted.holder_task == worker.task_id and adopted.run_id == worker.run_id
    stage_champion(worker.ctx(), adopted)
    worker.write_journal()
    worker.finish()
    return worker


def test_worker_died_after_submit_recovery_card_publishes_without_resubmit(board):
    a = Promoter(board, "pv_a")
    assert a.submit().ok
    a.worker.end_run("crashed")  # died before card finish; its branch exists
    pid = board.promotion().id

    [action] = reconcile(board.ctx(), board.queue, board.sm)

    assert action.kind == "recovery_requested" and action.promotion_id == pid
    spec = board.follow_up(action.request_id)["spec"]
    assert spec["priority"] == "P0" and spec["mode"] == "maintenance"
    assert f"promote-recover:{pid}" in spec["title"]
    assert spec["refs"] == [f"ar://experiment/{a.experiment}", f"ar://version/{a.version}"]
    assert reconcile(board.ctx(), board.queue, board.sm) == []  # one recovery card only

    _recover(board, action.request_id)
    [handled] = board.process()

    assert handled.state == "published"
    assert _stages(board.history(pid))[-2:] == ["recorded", "published"]
    assert (board.canonical / CHAMPION_PATH).read_text() == a.candidate_text
    assert len(board.submissions_of("pv_a")) == 1
    b = Promoter(board, "pv_b", baseline="pv_a")
    assert b.acquire()
    _clean(board)


def test_submit_succeeded_but_record_crashed_reconcile_finds_the_submission(board):
    a = Promoter(board, "pv_a")
    crashing = FakeSoftmax(board.canonical, {FAULTS_ENV: "fail_after=submit"})
    with pytest.raises(RuntimeError, match="injected fault after submit"):
        a.submit(sm=crashing)
    assert board.promotion().stage == "intent"
    assert len(board.submissions_of("pv_a")) == 1
    a.worker.end_run("crashed")

    actions = reconcile(board.ctx(), board.queue, board.sm)

    assert [action.kind for action in actions] == ["submitted", "recovery_requested"]
    current = board.promotion()
    assert current.stage == "submitted"
    assert current.submission_id == board.submissions_of("pv_a")[0]["submission_id"]
    _recover(board, actions[-1].request_id)
    [handled] = board.process()
    assert handled.state == "published"
    assert board.promotion().stage == "published"
    assert len(board.submissions_of("pv_a")) == 1  # never submitted twice


def test_intent_without_submission_is_aborted(board):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    a.worker.end_run("crashed")  # died between intent and submit

    [action] = reconcile(board.ctx(), board.queue, board.sm)

    assert action.kind == "aborted" and board.promotion().stage == "aborted"
    assert Promoter(board, "pv_b").acquire()


def test_intent_holder_still_running_is_left_alone(board):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    assert reconcile(board.ctx(), board.queue, board.sm) == []
    assert board.promotion().stage == "intent"


def test_holder_from_an_earlier_board_epoch_counts_as_terminal(board):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    _force(board, board_epoch="e0")  # the board that held the run is gone (C12)

    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "aborted"


def test_old_promotion_branch_after_another_published_is_reconciled(board):
    a = Promoter(board, "pv_a")
    assert a.acquire()
    board.queue.requeue(a.worker.task_id, "reclaimed")  # the run ends; the card goes back
    b = Promoter(board, "pv_b")
    assert b.submit().ok  # A's promotion was abandoned in `acquired`
    b.finish()
    [published] = board.process()
    assert published.state == "published"

    # A's card runs again and still carries the champion change from its old promotion.
    a.worker.start()
    (a.worker.worktree / CHAMPION_PATH).write_text(a.candidate_text)
    a.finish()
    [late] = board.process()

    assert late.state == "reconcile"
    assert "champion mismatch" in board.follow_up(late.follow_up_request)["spec"]["why"]
    assert (board.canonical / CHAMPION_PATH).read_text() == b.candidate_text
    assert board.promotion().stage == "published"


# --- champion ---


def test_current_champion_needs_ledger_and_leaderboard_to_agree(board):
    assert current_champion(board.ctx(), board.sm, LEAGUE) == BASELINE_PV  # nothing promoted
    a = Promoter(board, "pv_a")
    assert a.submit().ok
    a.finish()
    board.process()
    assert current_champion(board.ctx(), board.sm, LEAGUE) == "pv_a"

    board.sm.submit("pv_elsewhere", LEAGUE)  # the leaderboard moved without a promotion
    assert current_champion(board.ctx(), board.sm, LEAGUE) is None
    b = Promoter(board, "pv_b", baseline="pv_a")
    outcome = b.submit()
    assert outcome.ok is False and "unknown" in outcome.reason
    assert board.submissions_of("pv_b") == []


def test_adopt_only_by_the_recovery_card(board):
    a = Promoter(board, "pv_a")
    assert a.submit().ok
    other = board.admit(mode="maintenance")
    other.start()
    with pytest.raises(PromotionError, match="recovery"):
        adopt(other.ctx(), board.promotion().id)
    assert board.promotion().holder_task == a.worker.task_id

"""Promotion state machine (Task 3.6, Overview C8): real git with a bare ``origin``, FileQueue,
FakeSoftmax and simulated workers. The Board/Worker helpers come from ``tests/support/board.py``."""

import datetime
import json
import stat
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from support.board import Board, Worker

from ide_research_core.cli import main
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_json
from ide_research_core.queue.requests import load_schema
from ide_research_core.runtime import promotion
from ide_research_core.runtime.ledger import canonical_lock, commit
from ide_research_core.runtime.merge import find_orphan_done
from ide_research_core.runtime.promotion import (
    CHAMPION_PATH,
    PromotionError,
    accept_champion,
    acquire,
    adopt,
    champion_conflict,
    current_champion,
    load,
    reconcile,
    record_intent,
    stage_champion,
    submit_promotion,
)
from ide_research_core.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
from ide_research_core.softmax_tool.protocol import SubmissionState

LEAGUE = "league_fake"
# Platform policy version ids are opaque labels (the real client uses ``name:vN``).
BASELINE_PV = "autoresearch-fake:v0"  # leaderboard champion before any promotion (A9)
PV_A, PV_B = "autoresearch-fake:v1", "autoresearch-fake:v2"
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

    def __init__(
        self, board: PromotionBoard, platform_id: str, baseline: str = BASELINE_PV, refs=()
    ):
        self.board = board
        self.platform_id = platform_id
        self.experiment, self.version = board.new_experiment(platform_id, baseline)
        self.worker: Worker = board.admit(
            refs=[f"ar://experiment/{self.experiment}", *refs], mode="maintenance"
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
    a = Promoter(board, PV_A)
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    a.worker.end_run("done")
    _force(board, stage=stage, updated_at=_minutes_ago(20))

    b = Promoter(board, PV_B)
    assert b.acquire() is False
    assert board.promotion().holder_task == a.worker.task_id
    assert board.promotion().stage == stage


@pytest.mark.parametrize("stage", ["published", "aborted"])
def test_acquire_allowed_after_published_or_aborted(board, stage):
    a = Promoter(board, PV_A)
    assert a.acquire()
    _force(board, stage=stage)  # holder run still open: the stage alone frees the lock

    b = Promoter(board, PV_B)
    assert b.acquire()
    current = board.promotion()
    assert current.holder_task == b.worker.task_id and current.stage == "acquired"
    assert current.platform_policy_version_id == PV_B
    assert current.id.startswith("P-")


def test_acquire_takes_over_only_an_abandoned_acquired_promotion(board):
    a = Promoter(board, PV_A)
    assert a.acquire()
    assert a.acquire()  # idempotent for its own holder
    first = board.promotion()
    b = Promoter(board, PV_B)
    assert b.acquire() is False  # holder run still open

    a.worker.end_run("done")  # abandoned in `acquired`: no submit intent
    assert b.acquire()
    assert board.promotion().holder_task == b.worker.task_id
    assert board.history(first.id)["stage"] == "aborted"
    _clean(board)


# --- record_intent refusals ---


def test_record_intent_refuses_when_candidate_branch_push_fails(board):
    a = Promoter(board, PV_A)
    gitops.git(board.canonical, "remote", "set-url", "origin", str(board.tmp_path / "missing"))
    assert a.acquire()

    with pytest.raises(PromotionError, match="candidate branch"):
        record_intent(a.ctx(), board.promotion())
    assert board.promotion().stage == "acquired"

    outcome = a.submit()
    assert outcome.ok is False and "candidate branch" in outcome.reason
    assert board.promotion().stage == "acquired"
    assert board.submissions_of(PV_A) == []
    _clean(board)


def test_record_intent_refuses_when_ledger_push_fails(board):
    hook = board.origin / "hooks" / "update"
    hook.write_text('#!/bin/sh\n[ "$1" = "refs/heads/main" ] && exit 1\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    a = Promoter(board, PV_A)
    assert a.acquire()

    with pytest.raises(PromotionError, match="ledger"):
        record_intent(a.ctx(), board.promotion())

    current = board.promotion()
    assert current.stage == "acquired" and current.candidate_sha is None
    assert _stages(board.history(current.id)) == ["acquired", "intent", "acquired"]
    branch = f"refs/heads/card/{a.worker.request_id}"
    assert gitops.git(board.origin, "rev-parse", branch)  # the branch push itself worked
    assert a.submit().ok is False
    assert board.submissions_of(PV_A) == []
    _clean(board)


# --- the normal path ---


def test_promotion_merges_records_publishes_and_frees_the_lock(board):
    a = Promoter(board, PV_A)
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
    assert current_champion(board.ctx(), board.sm, LEAGUE) == PV_A
    assert len(board.submissions_of(PV_A)) == 1
    assert reconcile(board.ctx(), board.queue, board.sm) == []

    b = Promoter(board, PV_B, baseline=PV_A)
    assert b.acquire()  # on_published freed the lock
    _clean(board)


def test_champion_not_baseline_refuses_and_requests_reevaluation(board):
    a = Promoter(board, PV_A, baseline="autoresearch-fake:v-old")

    outcome = a.submit()

    assert (
        outcome.ok is False
        and "autoresearch-fake:v-old" in outcome.reason
        and BASELINE_PV in outcome.reason
    )
    assert board.submissions_of(PV_A) == []
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
    a = Promoter(board, PV_A)
    assert a.submit().ok
    (a.worker.worktree / CHAMPION_PATH).write_text("# something else\n")
    a.finish()

    [handled] = board.process()

    assert handled.state == "repair" and handled.follow_up_request is None
    record = a.worker.handoff()
    jsonschema.validate(record, load_schema("handoff.json"))
    assert record["promotion"] == board.promotion().id and "follow_up_request" not in record
    assert board.promotion().stage == "submitted"
    assert (board.canonical / CHAMPION_PATH).read_text() == CHAMPION_V0
    # the recovery card is the only follow-up
    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "recovery_requested"
    assert find_orphan_done(board.queue, board.ledger) == []
    recovery = _recover(board, action.request_id)
    [recovered] = board.process()
    assert recovered.state == "published" and board.promotion().stage == "published"
    # publishing closed the promotion-owned repair handoff and the holder's request
    record = a.worker.handoff()
    jsonschema.validate(record, load_schema("handoff.json"))
    assert record["state"] == "superseded"
    assert record["superseded_by"] == f"{recovery.task_id}/{recovery.run_id}"
    receipt = a.worker.receipt()
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "superseded" and receipt["superseded_by"] == board.promotion().id
    assert find_orphan_done(board.queue, board.ledger) == []
    _clean(board)


def _assert_covered_closed(board: PromotionBoard) -> None:
    """Rebuild-style check (C12): no covered request is left admitted or handed off."""
    current = board.promotion()
    original = current.candidate_branch.removeprefix("card/")
    for request_id in [original, *current.recovery_requests]:
        receipt = board.committed(board.receipt_path(request_id))
        assert receipt is not None, request_id
        jsonschema.validate(receipt, load_schema("receipt.json"))
        assert receipt["state"] in ("published", "superseded", "rejected"), receipt


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
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")  # died before card finish; its branch exists
    pid = board.promotion().id

    [action] = reconcile(board.ctx(), board.queue, board.sm)

    assert action.kind == "recovery_requested" and action.promotion_id == pid
    spec = board.follow_up(action.request_id)["spec"]
    assert spec["priority"] == "P0" and spec["mode"] == "maintenance"
    assert f"promote-recover:{pid}:1" in spec["title"]
    assert "promotion adopt" in spec["why"] and "stage-champion" in spec["why"]
    assert f"card/{a.worker.request_id}" in spec["why"]  # carry over its other changes
    assert spec["refs"] == [f"ar://experiment/{a.experiment}", f"ar://version/{a.version}"]
    assert reconcile(board.ctx(), board.queue, board.sm) == []  # one recovery card only

    _recover(board, action.request_id)
    [handled] = board.process()

    assert handled.state == "published"
    assert _stages(board.history(pid))[-2:] == ["recorded", "published"]
    assert (board.canonical / CHAMPION_PATH).read_text() == a.candidate_text
    assert len(board.submissions_of(PV_A)) == 1
    assert find_orphan_done(board.queue, board.ledger) == []  # the dead holder is covered
    assert a.worker.receipt()["superseded_by"] == pid
    _assert_covered_closed(board)
    b = Promoter(board, PV_B, baseline=PV_A)
    assert b.acquire()
    _clean(board)


def test_submit_succeeded_but_record_crashed_reconcile_finds_the_submission(board):
    a = Promoter(board, PV_A)
    crashing = FakeSoftmax(board.canonical, {FAULTS_ENV: "fail_after=submit"})
    with pytest.raises(RuntimeError, match="injected fault after submit"):
        a.submit(sm=crashing)
    assert board.promotion().stage == "intent"
    assert len(board.submissions_of(PV_A)) == 1
    a.worker.end_run("crashed")

    actions = reconcile(board.ctx(), board.queue, board.sm)

    assert [action.kind for action in actions] == ["submitted", "recovery_requested"]
    current = board.promotion()
    assert current.stage == "submitted"
    assert current.submission_id == board.submissions_of(PV_A)[0]["submission_id"]
    _recover(board, actions[-1].request_id)
    [handled] = board.process()
    assert handled.state == "published"
    assert board.promotion().stage == "published"
    assert len(board.submissions_of(PV_A)) == 1  # never submitted twice


def test_intent_without_submission_is_aborted(board):
    a = Promoter(board, PV_A)
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    a.worker.end_run("crashed")  # died between intent and submit

    [action] = reconcile(board.ctx(), board.queue, board.sm)

    assert action.kind == "aborted" and board.promotion().stage == "aborted"
    assert Promoter(board, PV_B).acquire()


def test_intent_holder_still_running_is_left_alone(board):
    a = Promoter(board, PV_A)
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    assert reconcile(board.ctx(), board.queue, board.sm) == []
    assert board.promotion().stage == "intent"


def test_holder_from_an_earlier_board_epoch_counts_as_terminal(board):
    a = Promoter(board, PV_A)
    assert a.acquire()
    record_intent(a.ctx(), board.promotion())
    _force(board, board_epoch="e0")  # the board that held the run is gone (C12)

    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "aborted"


def test_old_promotion_branch_after_another_published_is_reconciled(board):
    a = Promoter(board, PV_A)
    assert a.acquire()
    board.queue.requeue(a.worker.task_id, "reclaimed")  # the run ends; the card goes back
    b = Promoter(board, PV_B)
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


def test_the_league_alone_decides_the_champion(board):
    """A9: the champion is what the league shows for us, before and after promotions; a
    policy submitted by hand is the champion too, and a conflict for queue-keeper."""
    now = datetime.datetime.now(datetime.UTC)
    assert current_champion(board.ctx(), board.sm, LEAGUE) == BASELINE_PV  # nothing promoted
    assert champion_conflict(board.canonical, BASELINE_PV, now) is None  # no expectation yet
    a = Promoter(board, PV_A)
    assert a.submit().ok
    assert board.promotion().replaces == BASELINE_PV
    a.finish()
    board.process()
    assert current_champion(board.ctx(), board.sm, LEAGUE) == PV_A
    assert champion_conflict(board.canonical, PV_A, now) is None

    board.sm.submit("other-policy:v1", LEAGUE)  # submitted by hand: the league moved
    assert current_champion(board.ctx(), board.sm, LEAGUE) == "other-policy:v1"
    conflict = champion_conflict(board.canonical, "other-policy:v1", now)
    assert (conflict.expected, conflict.shown, conflict.waited_out) == (
        PV_A,
        "other-policy:v1",
        False,
    )
    # An experiment designed against the old champion no longer matches the league.
    b = Promoter(board, PV_B, baseline=PV_A)
    outcome = b.submit()
    assert outcome.ok is False and "not the experiment baseline" in outcome.reason
    assert board.submissions_of(PV_B) == []


def test_grace_window_while_our_submission_is_in_flight(board):
    """A9: while our submission is unconfirmed the league may still show the replaced policy;
    after the grace window that is a conflict. Any third policy is a conflict at once."""
    a = Promoter(board, PV_A)
    assert a.submit().ok
    submitted_at = datetime.datetime.fromisoformat(board.promotion().history[-1]["at"])
    grace = datetime.timedelta(minutes=30)
    soon = submitted_at + datetime.timedelta(minutes=5)
    late = submitted_at + datetime.timedelta(minutes=31)
    assert champion_conflict(board.canonical, PV_A, late, grace) is None  # confirmed
    assert champion_conflict(board.canonical, BASELINE_PV, soon, grace) is None  # not yet
    waited = champion_conflict(board.canonical, BASELINE_PV, late, grace)
    assert waited.waited_out and waited.expected == PV_A and waited.shown == BASELINE_PV
    third = champion_conflict(board.canonical, "other-policy:v1", soon, grace)
    assert third is not None and not third.waited_out


def test_our_submission_state_decides_while_the_league_shows_the_old_policy(board):
    """A9 grace from the league's own membership: qualifying is never a conflict; once
    admitted (competing, not champion) the league gets the grace window to make it champion;
    a rejection is an outcome (the loop then expects the old policy), not a conflict."""
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.finish()
    board.process()
    submitted_at = datetime.datetime.fromisoformat(board.promotion().history[1]["at"])
    grace = datetime.timedelta(minutes=30)
    days_later = submitted_at + datetime.timedelta(days=2)

    def conflict(shown, state, since=None):
        return champion_conflict(board.canonical, shown, days_later, grace, state, since)

    pending = SubmissionState("pending", detail="membership qualifying (held)")
    assert conflict(BASELINE_PV, pending) is None  # still qualifying, however long
    competing = SubmissionState("competing", False, "membership competing (benched)")
    assert conflict(BASELINE_PV, competing, days_later - datetime.timedelta(minutes=10)) is None
    waited = conflict(BASELINE_PV, competing, days_later - datetime.timedelta(minutes=31))
    assert waited.waited_out and "admitted" in waited.detail
    assert conflict(BASELINE_PV, competing) is None  # first seen admitted just now
    rejected = SubmissionState("rejected", detail="membership disqualified (inactive)")
    assert conflict(BASELINE_PV, rejected) is None  # the league kept the old champion
    other = conflict(None, rejected)
    assert other.expected == BASELINE_PV and other.shown is None and not other.waited_out
    assert conflict("hand-made:v1", pending).expected == PV_A  # a third policy, at once


def test_league_check_tracks_when_the_admission_was_first_seen(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.finish()
    board.process()
    player = board.sm.whoami().player_id
    state = json.loads((board.canonical / ".supervisor/runtime/fake_softmax.json").read_text())
    state["memberships"] = {PV_A: {"stage": "competing", "is_champion": False}}
    state["submissions"] = [s for s in state["submissions"] if s["policy_version_id"] != PV_A]
    (board.canonical / ".supervisor/runtime/fake_softmax.json").write_text(json.dumps(state))
    assert board.sm.leaderboard_champion(player, LEAGUE) == BASELINE_PV
    t0 = datetime.datetime.now(datetime.UTC)
    grace = datetime.timedelta(minutes=30)
    first = promotion.league_check(board.ctx(), board.sm, LEAGUE, t0, grace, record=True)
    assert first.conflict is None and first.submission.stage == "competing"
    later = t0 + datetime.timedelta(minutes=45)
    peek = promotion.league_check(board.ctx(), board.sm, LEAGUE, later, grace)
    assert peek.conflict is not None and peek.conflict.waited_out


def test_recovery_waits_while_the_league_has_not_decided(board):
    """A9 x A11: no recovery attempt is spent while our submission is still qualifying."""
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    state_path = board.canonical / ".supervisor/runtime/fake_softmax.json"
    real = json.loads(state_path.read_text())
    qualifying = {**real, "memberships": {PV_A: {"stage": "pending"}}}
    qualifying["submissions"] = [s for s in real["submissions"] if s["policy_version_id"] != PV_A]
    state_path.write_text(json.dumps(qualifying))
    assert reconcile(board.ctx(), board.queue, board.sm, LEAGUE) == []
    assert board.promotion().recovery_requests == []

    state_path.write_text(json.dumps(real))  # admitted and champion
    [action] = reconcile(board.ctx(), board.queue, board.sm, LEAGUE)
    assert action.kind == "recovery_requested"


def _league_rejects(board, policy: str) -> None:
    """The league disqualifies ``policy``'s membership and keeps the old champion."""
    state_path = board.canonical / ".supervisor/runtime/fake_softmax.json"
    state = json.loads(state_path.read_text())
    state["memberships"] = {policy: {"stage": "rejected"}}
    state["submissions"] = [s for s in state["submissions"] if s["policy_version_id"] != policy]
    state_path.write_text(json.dumps(state))


def test_a_rejected_submission_frees_the_slot_at_once(board):
    """N1: holder dead and the league rejects: reconcile settles the promotion as ``rejected``
    in one run, one message goes out, and a later submit is allowed."""
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    _league_rejects(board, PV_A)
    [action] = reconcile(board.ctx(), board.queue, board.sm, LEAGUE)
    assert action.kind == "rejected"
    rejected = board.promotion()
    assert rejected.stage == "rejected" and rejected.recovery_requests == []
    assert "the league rejected it" in rejected.history[-1]["note"]
    assert reconcile(board.ctx(), board.queue, board.sm, LEAGUE) == []  # settled once
    outbox = [
        json.loads(path.read_text())
        for path in sorted((board.canonical / "comms" / "outbox").glob("*.json"))
    ]
    messages = [m for m in outbox if "did not accept" in m["title"]]
    assert len(messages) == 1 and messages[0]["kind"] == "note"
    assert champion_conflict(board.canonical, BASELINE_PV, datetime.datetime.now(datetime.UTC)) is None

    b = Promoter(board, PV_B)  # the slot is free; the champion is still the old one
    assert b.submit().ok
    assert board.promotion().stage == "submitted" and board.promotion().replaces == BASELINE_PV


def test_a_pending_submission_asks_for_help_once_after_the_cap(board, monkeypatch):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    state_path = board.canonical / ".supervisor/runtime/fake_softmax.json"
    state = json.loads(state_path.read_text())
    state["memberships"] = {PV_A: {"stage": "pending"}}
    state["submissions"] = [s for s in state["submissions"] if s["policy_version_id"] != PV_A]
    state_path.write_text(json.dumps(state))
    assert reconcile(board.ctx(), board.queue, board.sm, LEAGUE) == []  # waiting, quietly
    monkeypatch.setattr(promotion, "PENDING_CAP", datetime.timedelta(0))
    [action] = reconcile(board.ctx(), board.queue, board.sm, LEAGUE)
    assert action.kind == "help_requested"
    assert reconcile(board.ctx(), board.queue, board.sm, LEAGUE) == []  # once
    assert board.promotion().stage == "submitted" and board.promotion().recovery_requests == []


def test_accepting_the_league_champion_resolves_the_conflict(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.finish()
    board.process()
    board.sm.submit("other-policy:v1", LEAGUE)
    now = datetime.datetime.now(datetime.UTC)
    assert champion_conflict(board.canonical, "other-policy:v1", now) is not None
    record = accept_champion(board.ctx(), board.sm, LEAGUE)
    assert record["policy_version_id"] == "other-policy:v1"
    assert champion_conflict(board.canonical, "other-policy:v1", now) is None
    # The acceptance holds until the league moves again; the loop's own policy agrees too.
    assert champion_conflict(board.canonical, "third:v1", now).expected == "other-policy:v1"
    assert champion_conflict(board.canonical, PV_A, now) is None
    # A newer promotion that reaches the league replaces the acceptance as the expectation.
    b = Promoter(board, PV_B, baseline="other-policy:v1")
    assert b.submit().ok
    assert board.promotion().replaces == "other-policy:v1"
    assert champion_conflict(board.canonical, PV_B, now) is None


def test_adopt_only_by_the_recovery_card(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    other = board.admit(mode="maintenance")
    other.start()
    with pytest.raises(PromotionError, match="recovery"):
        adopt(other.ctx(), board.promotion().id)
    assert board.promotion().holder_task == a.worker.task_id


# --- liveness (fix round 1) ---


def test_holder_handoff_reconciled_then_superseded_gets_recovery(board):
    hypothesis = f"ar://hypothesis/{board.hypothesis}"
    a = Promoter(board, PV_A, refs=[hypothesis])
    other = board.admit(refs=[hypothesis])
    other.start()
    assert a.submit().ok
    a.worker.store.update(board.hypothesis, {"lever": "SK.wait"})
    other.store.update(board.hypothesis, {"lever": "SK.rush"})
    other.write_journal()
    other.finish()
    assert [o.state for o in board.process()] == ["published"]
    a.finish()

    [held] = board.process()  # C4: both changed the hypothesis
    assert held.state == "reconcile" and board.promotion().stage == "submitted"
    follow_up = board.admit_follow_up(held.follow_up_request)
    follow_up.start()
    follow_up.write_journal()  # redoes the non-champion work only
    follow_up.finish()
    [done] = board.process()
    assert done.state == "published" and a.worker.handoff()["state"] == "superseded"
    assert board.promotion().stage == "submitted"  # the reconcile card is not the holder

    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "recovery_requested"
    _recover(board, action.request_id)
    [recovered] = board.process()
    assert recovered.state == "published"
    assert _stages(board.history(board.promotion().id))[-2:] == ["recorded", "published"]
    assert len(board.submissions_of(PV_A)) == 1


def test_record_submitted_crash_holder_handoff_waits_at_intent(board, monkeypatch):
    a = Promoter(board, PV_A)

    def crash(ctx, promotion_record, submission_id):
        raise RuntimeError("crash in record_submitted")

    monkeypatch.setattr(promotion, "record_submitted", crash)
    with pytest.raises(RuntimeError, match="record_submitted"):
        a.submit()
    monkeypatch.undo()
    assert board.promotion().stage == "intent"
    stage_champion(a.ctx(), board.promotion())  # the worker stages it and finishes
    record = a.finish()

    [waiting] = board.process()
    assert waiting.state == "validated" and board.promotion().stage == "intent"
    assert not gitops.contains(board.canonical, record["result_sha"])  # never merged at intent

    [action] = reconcile(board.ctx(), board.queue, board.sm)
    assert action.kind == "submitted"  # no recovery: the holder's handoff is still open
    [handled] = board.process()
    assert handled.state == "published"
    assert _stages(board.history(board.promotion().id))[-2:] == ["recorded", "published"]
    assert len(board.submissions_of(PV_A)) == 1


def test_recovery_card_that_dies_gets_a_second_attempt(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    [first] = reconcile(board.ctx(), board.queue, board.sm)
    first_card = board.admit_follow_up(first.request_id)
    first_card.start()
    adopt(first_card.ctx(), board.promotion().id)
    first_card.end_run("crashed")  # adopted, then died without a handoff
    assert find_orphan_done(board.queue, board.ledger) == []  # reconcile owns it

    [second] = reconcile(board.ctx(), board.queue, board.sm)

    assert second.kind == "recovery_requested" and second.request_id != first.request_id
    pid = board.promotion().id
    assert f"promote-recover:{pid}:2" in board.follow_up(second.request_id)["spec"]["title"]
    assert board.promotion().recovery_requests == [first.request_id, second.request_id]
    assert reconcile(board.ctx(), board.queue, board.sm) == []  # at most one live
    _recover(board, second.request_id)
    [handled] = board.process()
    assert handled.state == "published" and board.promotion().stage == "published"
    assert len(board.submissions_of(PV_A)) == 1
    assert find_orphan_done(board.queue, board.ledger) == []
    assert board.committed(board.receipt_path(first.request_id))["state"] == "superseded"
    _assert_covered_closed(board)


def test_recovery_attempts_are_capped_then_one_help_request(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    for attempt in range(1, promotion.MAX_RECOVERY_ATTEMPTS + 1):
        [action] = reconcile(board.ctx(), board.queue, board.sm)
        assert action.kind == "recovery_requested"
        card = board.admit_follow_up(action.request_id)
        card.start()
        card.end_run("crashed")  # every attempt dies before adopting

    [action] = reconcile(board.ctx(), board.queue, board.sm)

    assert action.kind == "help_requested"
    assert len(board.promotion().recovery_requests) == promotion.MAX_RECOVERY_ATTEMPTS
    outbox = [
        json.loads(path.read_text())
        for path in sorted((board.canonical / "comms" / "outbox").glob("*.json"))
    ]
    helps = [m for m in outbox if m.get("kind") == "help_request"]
    assert len(helps) == 1 and helps[0]["about"]["promotion"] == board.promotion().id
    assert helps[0]["refs"][0]["uri"] == f"ar://experiment/{a.experiment}"
    assert reconcile(board.ctx(), board.queue, board.sm) == []  # no more cards, no more help
    assert board.promotion().stage == "submitted"
    _clean(board)


def test_rate_limited_holder_retried_run_is_accepted(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    first_run = a.worker.run_id
    board.queue.requeue(a.worker.task_id, "rate_limited")  # the run ends; the task is ready

    assert reconcile(board.ctx(), board.queue, board.sm) == []  # a retry will come
    a.worker.start()  # the retried run; the staged champion is still in the worktree
    a.finish()
    [handled] = board.process()

    assert handled.state == "published"
    current = board.promotion()
    assert current.stage == "published" and current.run_id == a.worker.run_id != first_run
    assert current.recovery_requests == []


def test_no_champion_known_refusal_message(board):
    a = Promoter(board, PV_A)
    newcomer = FakeSoftmax(board.canonical, {"AUTORESEARCH_PLAYER_ID": "ply_new"})
    outcome = a.submit(sm=newcomer)
    assert outcome.ok is False and outcome.reason.startswith("no champion: the")


# --- the promotion CLI ---


def _cli(capsys, monkeypatch, worker: Worker, *argv) -> tuple[int, dict]:
    env = {
        "HERMES_HOME": str(worker.board.tmp_path / "home"),
        "AUTORESEARCH_WORKSPACE": str(worker.board.canonical),
        "HERMES_KANBAN_WORKSPACE": str(worker.worktree),
        "HERMES_KANBAN_TASK": worker.task_id,
        "HERMES_KANBAN_RUN_ID": worker.run_id,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    code = main(["promotion", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_recovery_through_the_promotion_cli(board, capsys, monkeypatch):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.worker.end_run("crashed")
    [action] = reconcile(board.ctx(), board.queue, board.sm)
    pid = board.promotion().id
    card = board.admit_follow_up(action.request_id)
    card.start()

    code, env = _cli(capsys, monkeypatch, card, "status")
    assert code == 0 and env["data"]["id"] == pid and env["data"]["stage"] == "submitted"
    code, env = _cli(capsys, monkeypatch, card, "stage-champion", pid)
    assert code == 1 and "adopt" in env["error"]["message"]  # not the holder yet
    code, env = _cli(capsys, monkeypatch, card, "adopt", pid)
    assert code == 0 and env["data"]["holder_task"] == card.task_id
    code, env = _cli(capsys, monkeypatch, card, "stage-champion", pid)
    assert code == 0 and Path(env["data"]["path"]).read_text() == a.candidate_text

    card.write_journal()
    card.finish()
    [handled] = board.process()
    assert handled.state == "published" and board.promotion().stage == "published"


def test_promotion_cli_needs_a_runtime_context(capsys, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    code = main(["promotion", "status"])
    assert code == 2 and "HERMES_HOME" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_stage_champion_git_failure_is_a_json_error(board, capsys, monkeypatch):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    _force(board, candidate_sha="0" * 40)  # not local, and the fetch below fails
    gitops.git(board.canonical, "remote", "set-url", "origin", str(board.tmp_path / "missing"))
    code, env = _cli(capsys, monkeypatch, a.worker, "stage-champion", board.promotion().id)
    assert code == 1 and "GitError" in env["error"]["message"]


def test_accept_champion_through_the_promotion_cli(board, capsys, monkeypatch, tmp_path):
    """The operator command reads the game's league (fakegame's ``fake-league``, where this
    board's fake player has no champion) and records what it shows. A card cannot run it."""
    worker = board.admit(mode="maintenance")
    worker.start()
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_SOFTMAX", "fake")
    code, env = _cli(capsys, monkeypatch, worker, "accept-champion")
    assert code == 1 and "human operator" in env["error"]["message"]
    assert not (board.ledger / "champion_accepted.json").exists()

    for name in ("HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(name)  # an operator shell
    code, env = main(["promotion", "accept-champion"]), json.loads(capsys.readouterr().out)
    assert code == 0, env
    assert env["data"]["policy_version_id"] is None
    assert env["data"]["league_id"] == "fake-league"
    assert board.committed(board.ledger / "champion_accepted.json") == env["data"]


def _status_champion(board, now, grace=datetime.timedelta(minutes=30)):
    """The ``status`` champion section, read from this board's fake league."""
    from ide_research_core.runtime import status

    def check(at, window):
        return promotion.league_check(board.ctx(), board.sm, LEAGUE, at, window)

    return status.champion_section(check, {}, now, [])


def _set_league_state(board, memberships=None, hand_made=None):
    path = board.canonical / ".supervisor/runtime/fake_softmax.json"
    state = json.loads(path.read_text())
    state["memberships"] = memberships or {}
    state["submissions"] = [s for s in state["submissions"] if s["policy_version_id"] != PV_A]
    if hand_made:
        state["submissions"].append({**state["submissions"][-1], "policy_version_id": hand_made})
    path.write_text(json.dumps(state))


@pytest.mark.parametrize("stage", ["rejected", "pending"])
def test_status_agrees_with_queue_keeper_when_the_league_keeps_the_old_champion(board, stage):
    """A rejected or still-qualifying submission is no conflict, however long ago."""
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.finish()
    board.process()
    _set_league_state(board, {PV_A: {"stage": stage, "is_champion": False}})
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=2)
    assert _status_champion(board, later) == {"policy": BASELINE_PV, "conflict": False}


def test_status_reports_a_hand_submitted_policy_as_a_conflict(board):
    a = Promoter(board, PV_A)
    assert a.submit().ok
    a.finish()
    board.process()
    _set_league_state(board, hand_made="hand-made:v1")
    now = datetime.datetime.now(datetime.UTC)
    assert _status_champion(board, now) == {"policy": "hand-made:v1", "conflict": True}

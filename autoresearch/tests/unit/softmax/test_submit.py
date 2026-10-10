"""``softmax_tool.submit``: every guard before the single submit path (Plan 03 Task 3.3)."""

import datetime
import json

import pytest
from support.promotion_board import BASELINE_PV, LEAGUE, PLAYER, PV_A, SubmitBoard

from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.runtime import promotion
from autoresearch.runtime.promotion import CHAMPION_PATH, PROMOTION_FILE_REL, load
from autoresearch.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
from autoresearch.softmax_tool.platform_cli import IdentityError
from autoresearch.softmax_tool.protocol import Refused
from autoresearch.softmax_tool.submit import submit


@pytest.fixture
def board(tmp_path):
    return SubmitBoard(tmp_path)


def run_submit(board, worker, experiment, sm=None, player=PLAYER):
    return submit(worker.ctx(), board.queue, sm or board.sm, experiment, LEAGUE, player_id=player)


def submissions_of(board, platform_id):
    return [s for s in board.sm.submissions(PLAYER) if s["policy_version_id"] == platform_id]


def refusal(board, worker, experiment, sm=None, player=PLAYER) -> Refused:
    with pytest.raises(Refused) as caught:
        run_submit(board, worker, experiment, sm, player)
    assert submissions_of(board, PV_A) == []
    return caught.value


def test_submit_happy_path_stages_champion_and_records_event(board):
    experiment, version = board.reviewed_experiment()
    worker = board.promoter(experiment)

    data = run_submit(board, worker, experiment)

    assert data["submission_id"] and data["platform_policy_version_id"] == PV_A
    assert len(submissions_of(board, PV_A)) == 1
    current = load(board.canonical)
    assert current.stage == "submitted" and current.id == data["promotion_id"]
    assert [entry["stage"] for entry in current.history] == ["acquired", "intent", "submitted"]
    staged = (worker.worktree / CHAMPION_PATH).read_text()
    assert staged == f"# Candidate for {experiment}\n"
    events = worker.store.find("version_event", version=version, kind="submitted")
    assert len(events) == 1
    event = worker.store.load(events[0])[0]
    assert event["data"]["submission_id"] == data["submission_id"]
    assert event["data"]["promotion_id"] == data["promotion_id"]


def test_ledger_write_order_is_intent_then_submit_then_submitted(board, monkeypatch):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    order = []
    real_submit = board.sm.submit

    def watching_submit(policy_version_id, league_id):
        committed = board.committed(board.ledger / "promotion.json")
        pushed = json.loads(gitops.show(board.origin, "main", PROMOTION_FILE_REL))
        order.append(("submit", load(board.canonical).stage, committed["stage"], pushed["stage"]))
        return real_submit(policy_version_id, league_id)

    real_intent, real_submitted = promotion.record_intent, promotion.record_submitted

    def intent(ctx, current, replaces=None):
        order.append(("intent",))
        real_intent(ctx, current, replaces=replaces)

    def submitted(ctx, current, submission_id):
        order.append(("submitted",))
        real_submitted(ctx, current, submission_id)

    monkeypatch.setattr(board.sm, "submit", watching_submit)
    monkeypatch.setattr(promotion, "record_intent", intent)
    monkeypatch.setattr(promotion, "record_submitted", submitted)

    run_submit(board, worker, experiment)

    # intent is committed and pushed before Softmax is called; submitted comes after
    assert order == [("intent",), ("submit", "intent", "intent", "intent"), ("submitted",)]
    assert board.committed(board.ledger / "promotion.json")["stage"] == "submitted"
    pushed = json.loads(gitops.show(board.origin, "main", PROMOTION_FILE_REL))
    assert pushed["stage"] == "submitted"


def test_refuses_when_decision_is_not_promote(board):
    experiment, _ = board.reviewed_experiment(decision="hold")
    error = refusal(board, board.promoter(experiment), experiment)
    assert error.reason == "decision_not_promote"
    assert load(board.canonical) is None  # no promotion acquired


def test_refuses_without_stored_outcome(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    (worker.worktree / "stats" / "outcomes" / f"{experiment}.json").unlink()
    assert refusal(board, worker, experiment).reason == "no_outcome"


def test_refuses_when_not_eligible(board):
    experiment, _ = board.reviewed_experiment(
        outcome={"promote_eligible": False, "failures": ["n_short"]}
    )
    error = refusal(board, board.promoter(experiment), experiment)
    assert error.reason == "not_eligible" and "n_short" in str(error)
    assert load(board.canonical) is None


def test_refuses_stale_outcome_when_rows_changed(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    rows = worker.worktree / "stats" / "rows" / f"{experiment}.jsonl"
    rows.write_text(rows.read_text() + '{"episode_id": "e2"}\n')
    error = refusal(board, worker, experiment)
    assert error.reason == "stale_outcome" and "rows" in str(error)


def test_refuses_stale_outcome_when_rows_missing(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    (worker.worktree / "stats" / "rows" / f"{experiment}.jsonl").unlink()
    assert refusal(board, worker, experiment).reason == "stale_outcome"


def test_refuses_stale_outcome_when_design_hash_differs(board):
    experiment, _ = board.reviewed_experiment(outcome={"design_sha256": "0" * 64})
    error = refusal(board, board.promoter(experiment), experiment)
    assert error.reason == "stale_outcome" and "design" in str(error)


def test_refuses_outcome_for_another_candidate(board):
    experiment, _ = board.reviewed_experiment(outcome={"candidate_platform_id": "other:v7"})
    assert refusal(board, board.promoter(experiment), experiment).reason == "stale_outcome"


def test_refuses_when_outcome_champion_is_not_current_champion(board):
    experiment, _ = board.reviewed_experiment(outcome={"champion": "older:v1"})
    error = refusal(board, board.promoter(experiment), experiment)
    assert error.reason == "champion_mismatch"
    assert load(board.canonical) is None


def test_refuses_when_leaderboard_champion_moved_since_evaluation(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    board.sm.submit("someone-else:v4", LEAGUE)  # leaderboard moved after stats evaluate
    assert refusal(board, worker, experiment).reason == "champion_mismatch"


def test_refuses_when_champion_unknown(tmp_path):
    board = SubmitBoard(tmp_path, seed_champion=False)  # no leaderboard entry, nothing promoted
    experiment, _ = board.reviewed_experiment()
    assert refusal(board, board.promoter(experiment), experiment).reason == "champion_unknown"


def test_refuses_while_paused_for_a_champion_conflict(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    ledger = board.canonical / ".supervisor" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "pause.json").write_text(
        json.dumps({"reason": "champion conflict: test", "paused_at": "t", "tasks": {}})
    )
    assert refusal(board, worker, experiment).reason == "champion_conflict"
    assert load(board.canonical) is None


@pytest.mark.parametrize("faults", ["identity=user", "player=ply_other", "expires_in=-60"])
def test_identity_fault_refuses_before_any_promotion(board, faults):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    faulty = FakeSoftmax(board.canonical, {FAULTS_ENV: faults})
    error = refusal(board, worker, experiment, sm=faulty)
    assert error.reason == "identity"
    assert load(board.canonical) is None


def test_fake_submit_itself_refuses_a_wrong_identity(board):
    """A running worker whose credential changed is stopped at the boundary (C6)."""
    for faults in ("identity=user", "player=ply_other"):
        faulty = FakeSoftmax(board.canonical, {FAULTS_ENV: faults})
        with pytest.raises(IdentityError):
            faulty.submit(PV_A, LEAGUE)
    assert submissions_of(board, PV_A) == []


def test_refuses_without_worker_worktree(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    ctx = board.ctx(card_id=worker.task_id, run_id=worker.run_id)
    with pytest.raises(Refused) as caught:
        submit(ctx, board.queue, board.sm, experiment, LEAGUE, player_id=PLAYER)
    assert caught.value.reason == "no_worker"


def test_refuses_while_another_promotion_holds_the_lock(board):
    first, _ = board.reviewed_experiment()
    run_submit(board, board.promoter(first), first)  # now `submitted`, in flight
    # evaluated against the new leaderboard champion, so only the promotion lock refuses it
    second, _ = board.reviewed_experiment(
        platform_id="autoresearch-fake:v2", outcome={"champion": PV_A}
    )
    with pytest.raises(Refused) as caught:
        run_submit(board, board.promoter(second), second)
    assert caught.value.reason == "promotion_refused"
    assert "lock" in str(caught.value)


def test_baseline_mismatch_inside_submit_promotion_is_a_refusal(board):
    """The stored outcome agrees with the leaderboard, but the frozen design's baseline does
    not: submit_promotion's own champion==baseline check refuses and aborts."""
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    # Same champion everywhere, different baseline in the canonical experiment record.
    meta_path = board.store.path_of(experiment)
    text = meta_path.read_text().replace(BASELINE_PV, "autoresearch-fake:v9")
    meta_path.write_text(text)
    with pytest.raises(Refused) as caught:
        run_submit(board, worker, experiment)
    assert caught.value.reason == "promotion_refused"
    assert "baseline" in str(caught.value)
    assert load(board.canonical).stage == "aborted"


def test_refused_carries_machine_readable_fields(board):
    experiment, _ = board.reviewed_experiment(decision="refute")
    error = refusal(board, board.promoter(experiment), experiment)
    assert error.to_dict() == {
        "reason": "decision_not_promote",
        "message": str(error),
        **({"details": error.details} if error.details else {}),
    }


def test_outcome_file_written_by_hand_without_hashes_is_stale(board):
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    path = worker.worktree / "stats" / "outcomes" / f"{experiment}.json"
    write_json(path, {"promote_eligible": True, "champion": BASELINE_PV})
    assert refusal(board, worker, experiment).reason == "stale_outcome"


def test_a_permit_refusal_keeps_the_promotion_at_intent_and_reconcile_submits_it(board):
    """The coach's IDE Controls ask before league submissions: ``permit_required``, nothing is
    submitted or staged, and the promotion waits at ``intent``. After the yes, reconcile
    submits the same policy version; the holder card's handoff (which staged nothing) becomes
    the promotion's repair, as for any submitted promotion whose holder did not stage it."""
    experiment, _ = board.reviewed_experiment()
    worker = board.promoter(experiment)
    refusing = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    refused = refusal(board, worker, experiment, sm=refusing)
    assert refused.reason == "permit_required"
    current = load(board.canonical)
    assert refused.details["promotion_id"] == current.id
    assert current.stage == "intent" and current.waiting == "permit"
    assert not (worker.worktree / CHAMPION_PATH).read_text().startswith("# Candidate")

    worker.write_journal()
    worker.finish(outcome="partial")
    [handled] = board.process()
    assert handled.state == "validated"  # waits at intent

    due = datetime.datetime.fromisoformat(current.waiting_detail["next_retry_after"])
    later = due + datetime.timedelta(minutes=1)
    actions = promotion.reconcile(board.ctx(), board.queue, board.sm, LEAGUE, now=later)
    assert [a.kind for a in actions][:2] == ["help_requested", "submitted"]
    assert load(board.canonical).stage == "submitted"
    assert len(submissions_of(board, PV_A)) == 1
    [handled] = board.process()
    assert handled.state == "repair"

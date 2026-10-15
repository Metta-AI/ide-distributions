"""Self-monitoring signals (Task 3.8, System Design §8 with §10 defaults): one fixture per
signal, plus persistence and escalation. Uses the protocol Board (real git, FileQueue)."""

import datetime
import json
from dataclasses import replace

import pytest
from support.board import Board
from ulid import ULID

from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.runtime.preflight import PreflightResult, preflight
from autoresearch.runtime.signals import (
    SignalConfig,
    compute_signals,
    escalation_marker_path,
    signals_state_path,
)
from autoresearch.softmax_tool.fake import FAULTS_ENV, FakeSoftmax

NOW = datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.UTC)
LEVER = "SK.last_hit"
VERSION = {
    "parents": [],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-fake",
}


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _at(minutes: float) -> str:
    """An ISO time ``minutes`` before NOW (negative: after)."""
    return (NOW - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _handoff(board: Board, at: str, created=(), changed=(), overlap=None, state="published"):
    task = f"K-{ULID()}"
    record = {
        "card": task,
        "run_id": "r-1",
        "request_id": new_id("card_request"),
        "mode": "investigation",
        "refs": [],
        "report": {
            "ok": True,
            "problems": [],
            "violations": {},
            "created": list(created),
            "changed_existing": list(changed),
        },
        "state": state,
        "history": [{"state": "committed", "at": at}],
    }
    if overlap is not None:
        record["overlap"] = list(overlap)
    write_json(board.ledger / "handoffs" / task / "r-1.json", record)
    return record


def _iterations(board: Board, count: int, start: float = 100, created=None) -> None:
    """``count`` card runs that changed no record (only their journal)."""
    for index in range(count):
        journal = new_id("journal")
        _handoff(board, _at(start - index), created=created or [journal])
    gitops.commit_all(board.canonical, f"{count} iterations")


def _codes(signals) -> list[str]:
    return sorted(signal.code for signal in signals)


def _compute(board: Board, now=NOW, cfg=None, **kwargs):
    return compute_signals(
        board.ctx(), board.queue, now, cfg or SignalConfig(), allow_no_remote=True, **kwargs
    )


def _experiment(board: Board, status: str = "designed", **fields) -> str:
    experiment = board.store.create(
        "experiment",
        {
            "hypothesis": board.hypothesis,
            "parents": [],
            "mode": "experimentation",
            "lever": LEVER,
            "baseline": "V-0",
            "change": {"components": [LEVER], "description": "Wait for range."},
            "evaluation_design": {"n_min": 10},
            "environment": {"game_id": "fakegame", "game_version": "65"},
            "status": "designed",
        },
    )
    path = ["built", "uploaded", "evaluating", "reviewed"]
    if status == "awaiting-credits":
        path = ["built", "uploaded", "awaiting-credits"]
    for step in path[: path.index(status) + 1] if status != "designed" else []:
        if step == "uploaded":
            version = board.store.create("version", dict(VERSION))
            binding = {"version_id": version, "platform_policy_version_id": "pv-1"}
            board.store.update(experiment, {"status": "uploaded", "candidate_binding": binding})
        elif step == "reviewed":
            decision = {"outcome": "hold", "reason": "No gain.", "decided_at": fields.pop("at")}
            board.store.update(experiment, {"status": "reviewed", "decision": decision})
        else:
            board.store.set_status(experiment, step)
    if fields:
        board.store.update(experiment, fields)
    return experiment


# --- defaults ---


def test_defaults_match_system_design_section_10():
    cfg = SignalConfig()
    assert cfg.spinning_iterations == 6
    assert cfg.build_failure_k == 3
    assert (cfg.plateau_experiments, cfg.plateau_days) == (8, 2)
    assert cfg.tool_error_repeats == 3
    assert cfg.long_iteration == datetime.timedelta(minutes=45)
    assert cfg.long_iteration_run == 3
    assert cfg.stale_plan_iterations == 6
    assert cfg.queue_growth_days == 2
    assert cfg.stale_card_days == 3
    assert cfg.merge_hotspot_conflicts == 2
    assert cfg.escalation_iterations == 3


def test_quiet_workspace_has_no_signals(board):
    assert _compute(board) == []


# --- one fixture per signal ---


def test_spinning_after_six_iterations_with_no_record_change(board):
    _iterations(board, 5)
    assert "spinning" not in _codes(_compute(board))
    _iterations(board, 1, start=50)
    [signal] = [s for s in _compute(board) if s.code == "spinning"]
    assert signal.entity is None and "6" in signal.detail


def test_a_record_change_breaks_spinning(board):
    _iterations(board, 5)
    _iterations(board, 1, start=50, created=[new_id("journal"), new_id("hypothesis")])
    assert "spinning" not in _codes(_compute(board))


def test_thrash_on_a_lever_a_refuted_mechanism_claim_covers(board):
    board.store.create(
        "claim",
        {
            "kind": "mechanism",
            "status": "refuted",
            "confidence": 0.9,
            "evidence": [],
            "revise_if": "A replay shows kill range changed.",
            "topics": [],
            "lever": LEVER,
        },
        "Waiting for kill range does not raise last hits.",
    )
    experiment = _experiment(board)
    gitops.commit_all(board.canonical, "thrash")
    [signal] = _compute(board)
    assert (signal.code, signal.entity) == ("thrash", experiment)


def test_plateau_after_m_completed_experiments_without_promotion(board):
    for minutes in (30, 20):
        _experiment(board, "reviewed", at=_at(minutes))
    gitops.commit_all(board.canonical, "experiments")
    cfg = replace(SignalConfig(), plateau_experiments=2)
    assert "plateau" in _codes(_compute(board, cfg=cfg))
    assert "plateau" not in _codes(_compute(board))  # default M = 8


def test_plateau_after_d_days_without_promotion(board):
    assert "plateau" not in _codes(_compute(board, now=datetime.datetime.now(datetime.UTC)))
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=2, hours=1)
    assert "plateau" in _codes(_compute(board, now=later))


def _tool_event(
    board: Board,
    minutes_ago: float,
    run: str = "1",
    error_type="Timeout",
    signature="terminal: $AUTORESEARCH_CLI softmax league-episodes",
    exit_code=None,
    task="t_1",
    **fields,
):
    path = board.canonical / ".supervisor" / "runtime" / "tool_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    moment = NOW - datetime.timedelta(minutes=minutes_ago)
    event = {
        "ts": moment.timestamp(), "task": task, "run": run, "tool": "terminal",
        "status": "error", "error_type": error_type, "cmd_signature": signature,
        "exit_code": exit_code,
        **fields,
    }
    with path.open("a") as handle:
        handle.write(json.dumps(event) + "\n")


def test_repeated_tool_error_same_command_in_three_runs(board):
    _tool_event(board, 10, run="1")
    _tool_event(board, 5, run="2")
    _tool_event(board, 3, run="3", error_type="Other")
    _tool_event(board, 2, run="3", signature="terminal: $AUTORESEARCH_CLI queue list")
    assert "repeated_tool_error" not in _codes(_compute(board))
    _tool_event(board, 1, run="1", task="t_2")
    [signal] = _compute(board)
    assert signal.code == "repeated_tool_error"
    assert signal.entity == "terminal: $AUTORESEARCH_CLI softmax league-episodes [Timeout]"


def test_retries_inside_one_run_count_once(board):
    for minutes in (5, 4, 3, 2, 1):
        _tool_event(board, minutes, run="1")
    assert _compute(board) == []


def test_unrelated_failures_do_not_add_up(board):
    """The box run: ten terminal failures of different commands in two hours escalated."""
    for run, signature in enumerate(["ls strategy", "sed -n 1,80p", "head -c 2000", "cat x"]):
        _tool_event(board, 10 - run, run=str(run), signature=f"terminal: {signature}")
    assert _compute(board) == []


def test_grep_without_match_is_not_an_error(board):
    for run in ("1", "2", "3"):
        _tool_event(board, 5, run=run, signature="terminal: grep -l opponent", exit_code=1)
    assert _compute(board) == []
    for run in ("1", "2", "3"):
        _tool_event(board, 4, run=run, signature="terminal: grep -l opponent", exit_code=2)
    assert "repeated_tool_error" in _codes(_compute(board))


def test_the_error_signature_is_part_of_the_key(board):
    """System Design §8: the same command AND error signature. A timeout and a platform
    refusal of one command are different problems."""
    for run, signature in (("1", "exit 124 timeout"), ("2", "exit 1 reason platform")):
        _tool_event(board, 5, run=run, error_type="tool_error", error_signature=signature)
    _tool_event(board, 4, run="3", error_type="tool_error", error_signature="exit 124 timeout")
    assert _compute(board) == []
    _tool_event(board, 3, run="4", error_type="tool_error", error_signature="exit 124 timeout")
    [signal] = _compute(board)
    assert signal.entity == (
        "terminal: $AUTORESEARCH_CLI softmax league-episodes [exit 124 timeout]"
    )


def test_validation_feedback_from_our_cli_is_not_an_error(board):
    """`records validate` exits 1 with problems in nearly every card: the card fixes them."""
    for run in ("1", "2", "3"):
        _tool_event(
            board, 5, run=run, error_type="tool_error", exit_code=1,
            signature="terminal: $AUTORESEARCH_CLI records validate --all",
            exit_program="$AUTORESEARCH_CLI", error_signature="exit 1 problems",
        )
    assert _compute(board) == []


def test_exit_1_is_judged_by_the_program_that_exited(board):
    """The exit code belongs to the last command of the line, not the first one."""
    for run in ("1", "2", "3"):  # `cli queue list | grep x` found nothing: an answer
        _tool_event(
            board, 5, run=run, exit_code=1, signature="terminal: $AUTORESEARCH_CLI queue list",
            exit_program="grep", error_signature="exit 1",
        )
    assert _compute(board) == []
    for run in ("1", "2", "3"):  # `grep -q x f; ls missing`: ls failed
        _tool_event(
            board, 4, run=run, exit_code=1, signature="terminal: grep -q x f",
            exit_program="ls", error_signature="exit 1",
        )
    assert "repeated_tool_error" in _codes(_compute(board))


def test_old_tool_errors_do_not_count(board):
    for minutes, run in ((600, "1"), (590, "2"), (580, "3")):
        _tool_event(board, minutes, run=run)
    assert _compute(board) == []


def _journal(board: Board, started_minutes_ago: float, duration_minutes: float) -> None:
    board.store.create(
        "journal",
        {
            "iteration": 1,
            "mode": "investigation",
            "card": None,
            "doctrine_version": 1,
            "started": _at(started_minutes_ago),
            "ended": _at(started_minutes_ago - duration_minutes),
        },
        "Did things.\n\n## Decisions\n\n- none\n",
    )


def test_long_iterations_three_in_a_row(board):
    _journal(board, 300, 50)
    _journal(board, 200, 50)
    _journal(board, 100, 30)
    gitops.commit_all(board.canonical, "journals")
    assert "long_iterations" not in _codes(_compute(board))
    _journal(board, 40, 46)
    gitops.commit_all(board.canonical, "journal")
    assert "long_iterations" not in _codes(_compute(board))  # 30, 46: not 3 in a row
    _journal(board, 30, 50)
    _journal(board, 20, 50)
    gitops.commit_all(board.canonical, "journals")
    assert "long_iterations" in _codes(_compute(board))


def test_stale_plan_after_six_iterations_without_progress(board, monkeypatch):
    monkeypatch.setenv("GIT_COMMITTER_DATE", _at(120))
    (board.canonical / "plan.md").write_text("# Plan\n\nFarm.\n")
    gitops.commit_all(board.canonical, "plan")
    for index in range(5):
        _handoff(board, _at(100 - index), created=[new_id("journal"), new_id("hypothesis")])
    gitops.commit_all(board.canonical, "work")
    assert "stale_plan" not in _codes(_compute(board))
    _handoff(board, _at(90), created=[new_id("journal"), new_id("hypothesis")])
    gitops.commit_all(board.canonical, "more work")
    assert "stale_plan" in _codes(_compute(board))
    monkeypatch.setenv("GIT_COMMITTER_DATE", _at(80))
    (board.canonical / "plan.md").write_text("# Plan\n\nScout.\n")
    gitops.commit_all(board.canonical, "replan")
    assert "stale_plan" not in _codes(_compute(board))


def _pending(board: Board, count: int) -> None:
    for _ in range(count):
        board.admit()


def test_queue_growth_over_two_days(board):
    _pending(board, 3)
    state = {"signals": {}, "pending_samples": [[_at(3 * 24 * 60), 1], [_at(24 * 60), 2]]}
    write_json(signals_state_path(board.ctx()), state)
    [signal] = [s for s in _compute(board) if s.code == "queue_growth"]
    assert "1" in signal.detail and "3" in signal.detail


def test_shrinking_queue_is_not_growth(board):
    _pending(board, 1)
    state = {"signals": {}, "pending_samples": [[_at(3 * 24 * 60), 4], [_at(24 * 60), 2]]}
    write_json(signals_state_path(board.ctx()), state)
    assert "queue_growth" not in _codes(_compute(board))


def test_queue_growth_needs_two_days_of_samples(board):
    _pending(board, 3)
    state = {"signals": {}, "pending_samples": [[_at(60), 1]]}
    write_json(signals_state_path(board.ctx()), state)
    assert "queue_growth" not in _codes(_compute(board))


def test_stale_card_older_than_three_days(board):
    worker = board.admit()  # admitted at 2026-10-07T00:00:00Z
    admitted = datetime.datetime(2026, 10, 7, tzinfo=datetime.UTC)
    assert _compute(board, now=admitted + datetime.timedelta(days=2)) == []
    stale = [
        s
        for s in _compute(board, now=admitted + datetime.timedelta(days=3, hours=1))
        if s.code == "stale_card"
    ]
    assert [s.entity for s in stale] == [worker.task_id]


def test_merge_hotspot_two_conflicts_on_one_entity(board):
    hypothesis = board.hypothesis
    _handoff(board, _at(20), overlap=[hypothesis], state="superseded")
    gitops.commit_all(board.canonical, "one conflict")
    assert _compute(board) == []
    _handoff(board, _at(10), overlap=[hypothesis], state="reconcile")
    gitops.commit_all(board.canonical, "two conflicts")
    [signal] = _compute(board)
    assert (signal.code, signal.entity) == ("merge_hotspot", hypothesis)


def test_identity_mismatch_from_preflight(board):
    result = PreflightResult(
        ok=False,
        reasons=["player is ply_other, expected ply_fake"],
        identity_problems=["player is ply_other, expected ply_fake"],
    )
    [signal] = _compute(board, preflight=result)
    assert signal.code == "identity_mismatch" and "ply_other" in signal.detail


def test_disk_problem_is_not_an_identity_mismatch(board):
    result = PreflightResult(ok=False, reasons=["only 10 MB free"])
    assert _compute(board, preflight=result) == []


def test_credits_exhausted_from_fake_softmax(board):
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "credits=0"})
    result = preflight({"AUTORESEARCH_PLAYER_ID": "ply_fake"}, sm, board.canonical, NOW)
    [signal] = _compute(board, preflight=result)
    assert signal.code == "credits_exhausted"


def test_credits_exhausted_from_an_awaiting_credits_experiment(board):
    experiment = _experiment(board, "awaiting-credits")
    gitops.commit_all(board.canonical, "credit refusal")
    [signal] = _compute(board)
    assert signal.code == "credits_exhausted" and experiment in signal.detail


# --- persistence and escalation ---


def _outbox_help(board: Board) -> list[dict]:
    outbox = board.canonical / "comms" / "outbox"
    messages = [read_json(path) for path in sorted(outbox.glob("*.json"))]
    return [m for m in messages if m.get("kind") == "help_request"]


def test_count_is_iterations_since_first_seen(board):
    _iterations(board, 6)
    [first] = _compute(board)
    assert first.count == 1
    _iterations(board, 2, start=40)
    [again] = _compute(board)
    assert again.count == 3 and again.first_seen == first.first_seen


def test_signal_that_stops_firing_is_forgotten(board):
    _iterations(board, 6)
    assert _compute(board)
    _iterations(board, 1, start=40, created=[new_id("journal"), new_id("hypothesis")])
    assert _compute(board) == []
    assert read_json(signals_state_path(board.ctx()))["signals"] == {}


def test_escalation_after_t_more_iterations_publishes_one_help_request(board):
    _iterations(board, 6)
    _compute(board)
    _iterations(board, 2, start=40)
    assert "escalation" not in _codes(_compute(board))  # 2 more iterations: not yet
    _iterations(board, 1, start=30)
    signals = _compute(board)
    assert _codes(signals) == ["escalation", "spinning"]
    [escalation] = [s for s in signals if s.code == "escalation"]
    assert escalation.entity == "spinning"
    [help_request] = _outbox_help(board)
    assert "spinning" in help_request["title"] and help_request["from"] == "runtime"
    marker = escalation_marker_path(board.canonical, "spinning", escalation.first_seen)
    assert board.committed(marker) is not None
    assert gitops.git(board.canonical, "status", "--porcelain") == ""

    _compute(board)
    assert len(_outbox_help(board)) == 1  # once per signal key


def test_escalation_marker_survives_loss_of_runtime_state(board):
    _iterations(board, 6)
    _compute(board)
    _iterations(board, 3, start=40)
    _compute(board)
    assert len(_outbox_help(board)) == 1
    signals_state_path(board.ctx()).unlink()  # disposable state lost (host rebuilt)
    _compute(board)
    _iterations(board, 3, start=20)
    assert "escalation" in _codes(_compute(board))
    assert len(_outbox_help(board)) == 1  # the committed marker dedupes it


def test_a_signal_that_clears_and_returns_escalates_again(board):
    _iterations(board, 6)
    _compute(board)
    _iterations(board, 3, start=40)
    _compute(board)
    assert len(_outbox_help(board)) == 1
    _iterations(board, 1, start=30, created=[new_id("journal"), new_id("hypothesis")])
    assert _compute(board) == []  # the episode ends
    later = NOW + datetime.timedelta(minutes=5)
    _iterations(board, 6, start=20)
    _compute(board, now=later)  # a new episode starts
    _iterations(board, 3, start=10)
    assert "escalation" in _codes(_compute(board, now=later))
    assert len(_outbox_help(board)) == 2


def test_failed_queue_keeper_steps_are_signals(board):
    signals = _compute(board, step_failures={"kb": "ValueError: bad claim", "push": "rejected"})
    found = {(s.code, s.entity) for s in signals}
    assert ("keeper_step_failed", "kb") in found and ("keeper_step_failed", "push") in found
    [kb] = [s for s in signals if s.entity == "kb"]
    assert "ValueError: bad claim" in kb.detail

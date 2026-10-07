import json

import jsonschema
import pytest

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.requests import load_schema
from autoresearch.runtime.admission import admit, admit_and_create
from autoresearch.runtime.briefing import BriefingInputs
from runtime.workspace import GAME_VERSION, make_spec

PROFILE = "autoresearch"


class CrashOnceQueue:
    """Wraps a queue; the first call of ``method`` raises (after doing the work if ``after``)."""

    def __init__(self, inner, method, after=False):
        self.inner, self.method, self.after, self.crashed = inner, method, after, False

    def __getattr__(self, name):
        target = getattr(self.inner, name)
        if name != self.method:
            return target

        def wrapper(*args, **kwargs):
            if not self.crashed:
                self.crashed = True
                if self.after:
                    target(*args, **kwargs)
                raise RuntimeError(f"simulated crash in {name}")
            return target(*args, **kwargs)

        return wrapper


def _receipt(canonical, spec):
    return read_json(canonical.layout.ledger / "requests" / f"{spec.request_id}.json")


def _admit(ctx, queue, spec, game):
    return admit_and_create(ctx, queue, spec, PROFILE, BriefingInputs(), game, GAME_VERSION)


def test_admit_and_create_happy_path(canonical, ctx, queue, game):
    spec = make_spec(refs=[f"ar://topic/{canonical.topic_id}"])

    result = _admit(ctx, queue, spec, game)

    assert result.ok and result.reasons == []
    card = queue.get(result.task_id)
    assert card.assignee == PROFILE and card.state == "pending"
    receipt = _receipt(canonical, spec)
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "admitted" and receipt["mode"] == "investigation"
    assert receipt["tasks"] == [{"task_id": result.task_id, "board_epoch": "e0"}]
    assert [entry["state"] for entry in receipt["history"]] == ["admitted"]
    assert gitops.git(canonical.root, "log", "-1", "--format=%s") == f"admit {spec.request_id}"
    assert gitops.git(canonical.root, "status", "--porcelain") == ""
    runtime = canonical.root / ".supervisor" / "runtime"
    assert read_json(runtime / "cards" / f"{result.task_id}.json") == {
        "task_id": result.task_id,
        "request_id": spec.request_id,
        "mode": "investigation",
        "refs": spec.refs,
        "finished": False,
    }
    briefing = (runtime / "briefings" / f"{result.task_id}.md").read_text()
    assert f"<!-- autoresearch-briefing task={result.task_id} -->" in briefing


def test_board_epoch_comes_from_board_json(canonical, ctx, queue, game):
    write_json(canonical.layout.ledger / "board.json", {"epoch": "e7"})
    spec = make_spec()
    result = _admit(ctx, queue, spec, game)
    assert _receipt(canonical, spec)["tasks"] == [{"task_id": result.task_id, "board_epoch": "e7"}]


def test_rejects_unresolved_refs(canonical, ctx, queue, game):
    missing = "ar://topic/T-01K6Z3V8QX0000000000000000"
    spec = make_spec(refs=[f"ar://topic/{canonical.topic_id}", missing])

    reasons = admit(canonical.root, spec, queue, game, GAME_VERSION)
    assert len(reasons) == 1 and missing in reasons[0]

    result = _admit(ctx, queue, spec, game)
    assert not result.ok and result.task_id is None and missing in result.reasons[0]
    receipt = _receipt(canonical, spec)
    assert receipt["state"] == "rejected" and missing in receipt["reason"]
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert queue.list() == []


def test_accepts_handoff_ref_present_at_head(canonical, queue, game):
    write_json(canonical.layout.ledger / "handoffs" / "K-1" / "r-1.json", {"state": "committed"})
    gitops.commit_all(canonical.root, "handoff")
    spec = make_spec(mode="maintenance", refs=["ar://handoff/K-1/r-1"])
    assert admit(canonical.root, spec, queue, game, GAME_VERSION) == []
    spec = make_spec(mode="maintenance", refs=["ar://handoff/K-1/r-2"])
    assert admit(canonical.root, spec, queue, game, GAME_VERSION)


def test_experimentation_needs_noise_for_current_game_version(canonical, ctx, queue, game):
    spec = make_spec(mode="experimentation", refs=[f"ar://hypothesis/{canonical.hypothesis_id}"])
    assert admit(canonical.root, spec, queue, game, GAME_VERSION) == ["needs_calibration"]

    canonical.write_noise(game_version="64")
    assert admit(canonical.root, spec, queue, game, GAME_VERSION) == ["needs_calibration"]
    result = _admit(ctx, queue, spec, game)
    assert result.reasons == ["needs_calibration"]
    # Retryable: no terminal receipt, so the request is admitted once calibration lands.
    assert not (canonical.layout.ledger / "requests" / f"{spec.request_id}.json").exists()

    canonical.write_noise(game_version=GAME_VERSION)
    assert _admit(ctx, queue, spec, game).ok


def test_non_experimentation_needs_no_noise(canonical, queue, game):
    assert admit(canonical.root, make_spec(mode="scouting"), queue, game, GAME_VERSION) == []


def test_second_planning_card_rejected_until_first_publishes(canonical, ctx, queue, game):
    first = make_spec(mode="planning")
    assert _admit(ctx, queue, first, game).ok

    second = make_spec(mode="planning")
    assert admit(canonical.root, second, queue, game, GAME_VERSION) == ["planning_owned"]

    # Finished but unpublished still owns planning; and the card is gone from the queue.
    receipt_path = canonical.layout.ledger / "requests" / f"{first.request_id}.json"
    receipt = read_json(receipt_path)
    task_id = receipt["tasks"][0]["task_id"]
    queue.drop(task_id, "finished elsewhere")
    receipt |= {"state": "handed_off", "handed_off": {"task_id": task_id, "run_id": "r-1"}}
    write_json(receipt_path, receipt)
    gitops.commit_all(canonical.root, "handed off")
    assert admit(canonical.root, second, queue, game, GAME_VERSION) == ["planning_owned"]

    receipt |= {"state": "published", "merge_sha": gitops.head(canonical.root)}
    write_json(receipt_path, receipt)
    gitops.commit_all(canonical.root, "published")
    assert admit(canonical.root, second, queue, game, GAME_VERSION) == []


def test_planning_card_on_the_queue_without_receipt_owns_planning(canonical, queue, game):
    scheduled = make_spec(mode="planning")
    queue.create_unassigned(scheduled, "card/x", canonical.root / ".worktrees" / "x")
    assert admit(canonical.root, make_spec(mode="planning"), queue, game, GAME_VERSION) == [
        "planning_owned"
    ]
    # The request's own card (created before a crash) never blocks it.
    assert admit(canonical.root, scheduled, queue, game, GAME_VERSION) == []


def test_crash_before_assign_rerun_finishes(canonical, ctx, queue, game):
    spec = make_spec(refs=[f"ar://topic/{canonical.topic_id}"])
    crashing = CrashOnceQueue(queue, "assign")

    with pytest.raises(RuntimeError, match="simulated crash"):
        _admit(ctx, crashing, spec, game)
    [card] = queue.list()
    assert card.assignee is None and _receipt(canonical, spec)["state"] == "admitted"

    result = _admit(ctx, crashing, spec, game)

    assert result.ok and result.task_id == card.task_id
    assert [c.task_id for c in queue.list()] == [card.task_id]
    assert queue.get(card.task_id).assignee == PROFILE
    assert len(_receipt(canonical, spec)["history"]) == 1


def test_crash_after_create_before_receipt_rerun_finds_same_task(canonical, ctx, queue, game):
    spec = make_spec(mode="planning")
    crashing = CrashOnceQueue(queue, "create_unassigned", after=True)

    with pytest.raises(RuntimeError, match="simulated crash"):
        _admit(ctx, crashing, spec, game)
    assert not (canonical.layout.ledger / "requests" / f"{spec.request_id}.json").exists()

    result = _admit(ctx, crashing, spec, game)

    assert result.ok and [c.task_id for c in queue.list()] == [result.task_id]
    assert queue.get(result.task_id).assignee == PROFILE


def test_rerun_after_success_is_a_noop(canonical, ctx, queue, game):
    spec = make_spec()
    first = _admit(ctx, queue, spec, game)
    head = gitops.head(canonical.root)
    assert _admit(ctx, queue, spec, game) == first
    assert gitops.head(canonical.root) == head and len(queue.list()) == 1


def test_no_admission_while_paused(canonical, ctx, queue, game):
    write_json(
        canonical.layout.ledger / "pause.json", {"reason": "x", "paused_at": "t", "tasks": {}}
    )
    spec = make_spec()
    assert admit(canonical.root, spec, queue, game, GAME_VERSION) == ["paused"]
    result = _admit(ctx, queue, spec, game)
    assert result.reasons == ["paused"] and queue.list() == []
    assert not (canonical.layout.ledger / "requests" / f"{spec.request_id}.json").exists()


def test_receipt_is_valid_json_in_git(canonical, ctx, queue, game):
    spec = make_spec()
    _admit(ctx, queue, spec, game)
    path = f".supervisor/ledger/requests/{spec.request_id}.json"
    assert json.loads(gitops.show(canonical.root, "HEAD", path))["state"] == "admitted"

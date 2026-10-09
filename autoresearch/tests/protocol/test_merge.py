"""Handoff processing and merge (Task 3.4): real git, FileQueue, simulated workers that run in
worktrees and end with ``card finish`` (helpers in ``tests/support/board.py``)."""

import jsonschema
import pytest
from support.board import HYPOTHESIS_BODY, Board, ForeignBoard, new_hypothesis

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.kanban_queue import KanbanError
from autoresearch.queue.requests import load_schema
from autoresearch.runtime import merge
from autoresearch.runtime.merge import find_orphan_done


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _states(record: dict) -> list[str]:
    return [entry["state"] for entry in record["history"]]


def _assert_clean(board: Board) -> None:
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


# --- merging ---


def test_disjoint_new_entities_both_merge_in_finish_order(board):
    a, b = board.admit(), board.admit()
    a.start()
    b.start()
    a_id, b_id = new_hypothesis(a), new_hypothesis(b)
    a.write_journal()
    b.write_journal()
    b_record = b.finish()

    [outcome_b] = board.process()
    assert outcome_b.state == "published" and outcome_b.task_id == b.task_id

    a_record = a.finish()
    [outcome_a] = board.process()

    assert outcome_a.state == "published"
    assert gitops.contains(board.canonical, a_record["result_sha"])
    assert gitops.contains(board.canonical, b_record["result_sha"])
    assert board.store.path_of(a_id).exists() and board.store.path_of(b_id).exists()
    for worker, outcome in ((a, outcome_a), (b, outcome_b)):
        record = worker.handoff()
        jsonschema.validate(record, load_schema("handoff.json"))
        assert _states(record) == ["committed", "validated", "merged", "published"]
        assert record["merge_sha"] == outcome.merge_sha
        receipt = worker.receipt()
        jsonschema.validate(receipt, load_schema("receipt.json"))
        assert receipt["state"] == "published" and receipt["merge_sha"] == outcome.merge_sha
        subject = gitops.git(board.canonical, "log", "-1", "--format=%s", outcome.merge_sha)
        assert subject == f"merge card/{worker.request_id} ({worker.task_id}/{worker.run_id})"
    assert len(board.merge_commits()) == 2
    assert board.process() == []  # nothing left to do
    _assert_clean(board)


def test_same_entity_non_overlapping_lines_reconciles_even_though_git_would_merge(board):
    refs = [f"ar://hypothesis/{board.hypothesis}"]
    a, b = board.admit(refs), board.admit(refs, mode="maintenance")
    a.start()
    b.start()
    a.store.update(board.hypothesis, {"lever": "SK.wait"})
    b.store.update(board.hypothesis, {}, body=HYPOTHESIS_BODY.replace("five", "5"))
    a.write_journal()
    b.write_journal()
    a.finish()
    b_record = b.finish()

    outcomes = {outcome.task_id: outcome for outcome in board.process()}

    assert outcomes[a.task_id].state == "published"
    reconcile = outcomes[b.task_id]
    assert reconcile.state == "reconcile"
    assert not gitops.contains(board.canonical, b_record["result_sha"])
    record = b.handoff()
    jsonschema.validate(record, load_schema("handoff.json"))
    assert record["state"] == "reconcile"
    assert record["overlap"] == [board.hypothesis]
    assert record["follow_up_request"] == reconcile.follow_up_request
    request = board.follow_up(reconcile.follow_up_request)
    jsonschema.validate(request, load_schema("card_request.json"))
    spec = request["spec"]
    assert spec["priority"] == "P0" and spec["created_by"] == "runtime"
    assert spec["mode"] == "maintenance"
    assert spec["refs"] == refs + [f"ar://handoff/{b.task_id}/{b.run_id}"]
    assert b_record["result_sha"] in spec["why"] and board.hypothesis in spec["why"]
    # The canonical sha the result was checked against: HEAD before the reconcile commit.
    assert gitops.git(board.canonical, "rev-parse", "HEAD~1") in spec["why"]
    # The original receipt stays handed off until the reconcile card publishes.
    assert b.receipt()["state"] == "handed_off"
    assert len(board.merge_commits()) == 1
    assert board.process() == []
    _assert_clean(board)


def test_textual_conflict_elsewhere_reconciles(board):
    a, b = board.admit(), board.admit()
    a.start()
    b.start()
    (a.worktree / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: push.\n")
    (b.worktree / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: defend.\n")
    a.write_journal()
    b.write_journal()
    a.finish()
    b.finish()

    outcomes = {outcome.task_id: outcome for outcome in board.process()}

    assert outcomes[a.task_id].state == "published"
    assert outcomes[b.task_id].state == "reconcile"
    record = b.handoff()
    assert record["conflicts"] == ["strategy/notes.md"]
    assert record["overlap"] == []
    why = board.follow_up(record["follow_up_request"])["spec"]["why"]
    assert "strategy/notes.md" in why and record["result_sha"] in why
    assert (board.canonical / "strategy" / "notes.md").read_text().endswith("push.\n")
    _assert_clean(board)


def test_handoff_whose_run_is_not_terminal_is_not_processed(board):
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish(terminal=False)
    head = gitops.head(board.canonical)

    assert board.process() == []
    assert a.handoff()["state"] == "committed"
    assert gitops.head(board.canonical) == head

    a.end_run()
    [outcome] = board.process()
    assert outcome.state == "published"


# --- crash recovery ---


def test_crash_after_merge_before_state_write_converges_without_remerge_or_revalidation(
    board, monkeypatch
):
    a = board.admit()
    a.start()
    a.write_journal()
    record = a.finish()

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash after the merge commit")

    with monkeypatch.context() as patch:
        patch.setattr(merge, "_mark_merged", crash)
        with pytest.raises(RuntimeError):
            board.process()
    assert gitops.contains(board.canonical, record["result_sha"])
    assert a.handoff()["state"] == "validated"
    merges = board.merge_commits()
    assert len(merges) == 1
    (board.canonical / "AGENTS.md").write_text("# Doctrine v2\n")  # canonical moves on
    gitops.commit_all(board.canonical, "unrelated canonical commit")

    def no_validation(*args, **kwargs):
        raise AssertionError("a merged result must not be revalidated")

    monkeypatch.setattr(merge, "validate_at", no_validation)
    [outcome] = board.process()

    assert outcome.state == "published" and outcome.merge_sha == merges[0]
    assert board.merge_commits() == merges
    assert _states(a.handoff()) == ["committed", "validated", "merged", "published"]
    assert a.receipt()["merge_sha"] == merges[0]
    _assert_clean(board)


def test_committed_handoff_already_in_canonical_is_merged_without_validation(board, monkeypatch):
    a = board.admit()
    a.start()
    a.write_journal()
    record = a.finish()
    gitops.git(board.canonical, "merge", "--no-ff", "-m", "manual merge", record["result_sha"])
    manual = gitops.head(board.canonical)
    (board.canonical / "AGENTS.md").write_text("# Doctrine v2\n")
    gitops.commit_all(board.canonical, "unrelated canonical commit")

    def no_validation(*args, **kwargs):
        raise AssertionError("a merged result must not be revalidated")

    monkeypatch.setattr(merge, "validate_at", no_validation)
    [outcome] = board.process()

    assert outcome.state == "published" and outcome.merge_sha == manual
    assert _states(a.handoff()) == ["committed", "merged", "published"]
    assert board.merge_commits() == [manual]


def test_crash_in_publish_reruns_publish_then_marks_published(board):
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish()
    calls = []

    def flaky_publish(canonical, merge_sha, handoff):
        calls.append((canonical, merge_sha, handoff["card"]))
        if len(calls) == 1:
            raise RuntimeError("simulated crash in publish")

    with pytest.raises(RuntimeError):
        board.process(publish=flaky_publish)
    assert a.handoff()["state"] == "merged"
    merge_sha = a.handoff()["merge_sha"]

    [outcome] = board.process(publish=flaky_publish)

    assert outcome.state == "published"
    assert calls == [(board.canonical, merge_sha, a.task_id)] * 2
    assert a.receipt()["state"] == "published"
    assert len(board.merge_commits()) == 1


def test_crash_after_writing_follow_up_before_commit_reuses_the_request(board, monkeypatch):
    a = board.admit()
    a.start()
    (a.worktree / "strategy" / "x.md").write_text("no journal\n")
    a.finish(outcome="failed")  # invalid content is recorded for a failed outcome

    real_commit = merge.commit

    def crash_on_repair(canonical, paths, message):
        if message.endswith(": repair"):
            raise RuntimeError("simulated crash before the repair commit")
        return real_commit(canonical, paths, message)

    with monkeypatch.context() as patch:
        patch.setattr(merge, "commit", crash_on_repair)
        with pytest.raises(RuntimeError):
            board.process()
    requests = board.canonical / "cards" / "requests"
    written = [p for p in sorted(requests.glob("Q-*.json")) if p.stem != a.request_id]
    assert len(written) == 1

    [outcome] = board.process()

    assert outcome.state == "repair"
    assert [p for p in sorted(requests.glob("Q-*.json")) if p.stem != a.request_id] == written
    assert outcome.follow_up_request == written[0].stem
    _assert_clean(board)


# --- repair and supersession ---


def test_invalid_content_writes_repair_request_and_repair_publication_supersedes(board):
    refs = [f"ar://hypothesis/{board.hypothesis}"]
    a = board.admit(refs)
    a.start()
    (a.worktree / "strategy" / "x.md").write_text("forgot the journal\n")
    a_record = a.finish(outcome="failed")
    assert a_record["report"]["ok"] is False

    [outcome] = board.process()

    assert outcome.state == "repair"
    record = a.handoff()
    jsonschema.validate(record, load_schema("handoff.json"))
    assert record["state"] == "repair" and record["follow_up_request"] == outcome.follow_up_request
    handoff_ref = f"ar://handoff/{a.task_id}/{a.run_id}"
    spec = board.follow_up(outcome.follow_up_request)["spec"]
    assert spec["refs"] == refs + [handoff_ref]
    assert spec["priority"] == "P0" and spec["created_by"] == "runtime"
    assert "journal" in spec["why"]
    assert not gitops.contains(board.canonical, a_record["result_sha"])
    assert board.merge_commits() == []

    repair = board.admit_follow_up(outcome.follow_up_request)
    repair.start()
    repair.write_journal()
    repair.finish()
    [repaired] = board.process()

    assert repaired.state == "published"
    original = a.handoff()
    jsonschema.validate(original, load_schema("handoff.json"))
    assert original["state"] == "superseded"
    assert original["superseded_by"] == f"{repair.task_id}/{repair.run_id}"
    receipt = a.receipt()
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "superseded"
    assert receipt["superseded_by"] == f"{repair.task_id}/{repair.run_id}"
    assert repair.receipt()["state"] == "published"
    head = gitops.head(board.canonical)
    assert board.process() == []  # the superseded handoff is never processed again
    assert gitops.head(board.canonical) == head
    _assert_clean(board)


def test_done_task_without_handoff_gets_one_repair_request(board):
    refs = [f"ar://hypothesis/{board.hypothesis}"]
    a = board.admit(refs)
    a.start()
    a.end_run("done")  # the worker completed without running card finish

    [request_id] = find_orphan_done(board.queue, board.ledger)

    spec = board.follow_up(request_id)["spec"]
    assert spec["refs"] == refs + [f"ar://handoff/{a.task_id}/{a.run_id}#missing"]
    assert spec["priority"] == "P0" and spec["created_by"] == "runtime"
    marker = board.committed(board.ledger / "orphans" / f"{a.task_id}-{a.run_id}.json")
    assert marker["follow_up_request"] == request_id
    assert find_orphan_done(board.queue, board.ledger) == []  # deduplicated
    _assert_clean(board)

    # The missing-handoff ref resolves (to the marker), so admission can admit the request.
    from autoresearch.comms.resolver import resolve

    assert resolve(board.canonical, spec["refs"][-1]).path.endswith(
        f"orphans/{a.task_id}-{a.run_id}.json"
    )
    repair = board.admit_follow_up(request_id)
    repair.start()
    repair.write_journal()
    repair.finish()
    [outcome] = board.process()
    assert outcome.state == "published"
    assert a.receipt()["state"] == "superseded"
    assert a.receipt()["superseded_by"] == f"{repair.task_id}/{repair.run_id}"


def test_orphan_scan_ignores_dropped_cards_and_cards_with_handoffs(board):
    dropped = board.admit()
    board.queue.drop(dropped.task_id, "not needed")
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish()

    assert find_orphan_done(board.queue, board.ledger) == []


# --- foreign cards (created outside admission) ---


def _help_requests(board: Board) -> list[dict]:
    outbox = board.canonical / "comms" / "outbox"
    messages = [read_json(path) for path in sorted(outbox.glob("*.json"))]
    return [message for message in messages if message["kind"] == "help_request"]


def _foreign_finished_first(board: Board, epoch: str | None = None):
    """A foreign card and a normal card, both finished; the foreign handoff is the older one,
    so it is reached first. The foreign card is the box's shape: no committed create request,
    a receipt its worker wrote (naming board epoch ``epoch`` if given) and a handoff written
    past ``card finish``'s refusal."""
    foreign = board.admit(committed_request=False)
    normal = board.admit()
    if epoch is not None:
        path = board.receipt_path(foreign.request_id)
        receipt = read_json(path)
        receipt["tasks"][0]["board_epoch"] = epoch
        write_json(path, receipt)
        gitops.commit_all(board.canonical, "forged receipt epoch")
    foreign.start()
    normal.start()
    new_hypothesis(foreign)
    foreign.write_journal()
    foreign_record = foreign.finish(forged=True)
    normal.write_journal()
    normal.finish()
    return foreign, normal, foreign_record


def _assert_foreign_unmerged(board: Board, foreign, record: dict, title: str = "T") -> None:
    assert foreign.handoff()["state"] == "committed"
    assert not gitops.contains(board.canonical, record["result_sha"])
    marker = board.committed(board.ledger / "foreign" / f"{foreign.task_id}.json")
    assert marker["task_id"] == foreign.task_id and marker["title"] == title
    [help_request] = _help_requests(board)
    assert help_request["about"]["card"] == foreign.task_id
    assert title in help_request["title"]
    assert "operator" in help_request["body"]  # its worker wrote runtime records itself


def test_foreign_card_is_skipped_and_reported_once_while_other_handoffs_merge(board):
    foreign, normal, foreign_record = _foreign_finished_first(board)
    queue = ForeignBoard(board.queue, foreign.task_id, title="Design a damage-first tune")

    [outcome] = board.process(queue=queue)

    assert (outcome.task_id, outcome.state) == (normal.task_id, "published")
    _assert_foreign_unmerged(board, foreign, foreign_record, "Design a damage-first tune")
    assert "archive" in _help_requests(board)[0]["body"]
    _assert_clean(board)

    head = gitops.head(board.canonical)
    assert board.process(queue=queue) == []  # second run: nothing new, no second message
    assert gitops.head(board.canonical) == head
    assert len(_help_requests(board)) == 1


def test_provenance_not_the_board_decides_foreign(board):
    """Even when the board shows a readable card body, a handoff with no committed request
    is foreign."""
    foreign, normal, foreign_record = _foreign_finished_first(board)

    [outcome] = board.process()

    assert outcome.task_id == normal.task_id
    _assert_foreign_unmerged(board, foreign, foreign_record)


def test_foreign_handoff_is_not_merged_after_a_board_rebuild_before_any_handoffs_step(board):
    foreign, _normal, foreign_record = _foreign_finished_first(board)
    write_json(board.ledger / "board.json", {"epoch": "e2"})  # rebuilt before it was seen
    gitops.commit_all(board.canonical, "new board epoch")

    board.process(queue=ForeignBoard(board.queue, foreign.task_id))

    _assert_foreign_unmerged(board, foreign, foreign_record, "Hand-made card")


def test_a_forged_receipt_naming_an_earlier_epoch_is_still_foreign(board):
    """The forger controls the receipt's board epoch; an earlier epoch skips the board lookup."""
    foreign, _normal, foreign_record = _foreign_finished_first(board, epoch="e0")

    board.process(queue=ForeignBoard(board.queue, foreign.task_id))

    _assert_foreign_unmerged(board, foreign, foreign_record, "Hand-made card")


def test_an_unreadable_body_on_an_admitted_card_is_retried_not_foreign(board):
    a, b = board.admit(), board.admit()
    a.start()
    b.start()
    a.write_journal()
    a.finish()
    b.write_journal()
    b.finish()

    with pytest.raises(RuntimeError, match=a.task_id):
        board.process(queue=ForeignBoard(board.queue, a.task_id))

    assert b.handoff()["state"] == "published"
    assert a.handoff()["state"] == "committed"
    assert board.committed(board.ledger / "foreign" / f"{a.task_id}.json") is None
    assert _help_requests(board) == []
    [outcome] = board.process()  # the body reads again: it merges
    assert (outcome.task_id, outcome.state) == (a.task_id, "published")


class _FailingShowQueue:
    """Hermes cannot show one task (any other KanbanError)."""

    def __init__(self, queue, broken: str):
        self.queue, self.broken = queue, broken

    def get(self, task_id):
        if task_id == self.broken:
            raise KanbanError(["hermes", "kanban", "show", task_id], "database is locked")
        return self.queue.get(task_id)


def test_a_card_the_board_cannot_show_does_not_stop_the_other_handoffs(board):
    broken, normal = board.admit(), board.admit()
    for worker in (broken, normal):
        worker.start()
        worker.write_journal()
        worker.finish()
    queue = _FailingShowQueue(board.queue, broken.task_id)

    with pytest.raises(RuntimeError, match=broken.task_id):
        board.process(queue=queue)

    assert normal.handoff()["state"] == "published"
    assert broken.handoff()["state"] == "committed"  # retried next run
    [outcome] = board.process()
    assert (outcome.task_id, outcome.state) == (broken.task_id, "published")


# --- board epochs and receipts ---


class _NoLookupQueue:
    def get(self, task_id):
        raise AssertionError("an earlier-epoch handoff must be adopted without a run lookup")


class _EmptyQueue:
    def get(self, task_id):
        raise KeyError(task_id)


def test_earlier_epoch_handoff_is_adopted_without_run_lookup(board):
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish(terminal=False)
    write_json(board.ledger / "board.json", {"epoch": "e2"})  # the board was rebuilt
    gitops.commit_all(board.canonical, "new board epoch")

    [outcome] = board.process(queue=_NoLookupQueue())

    assert outcome.state == "published"
    receipt = a.receipt()
    assert receipt["state"] == "published" and receipt["merge_sha"] == outcome.merge_sha


def test_unknown_task_in_the_current_epoch_keeps_waiting(board):
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish()

    assert board.process(queue=_EmptyQueue()) == []
    assert a.handoff()["state"] == "committed"


def test_receipt_left_admitted_is_reconciled_to_handed_off(board):
    a = board.admit()
    a.start()
    a.write_journal()
    a.finish()
    receipt_file = board.receipt_path(a.request_id)
    receipt = read_json(receipt_file)
    write_json(
        receipt_file,
        {key: receipt[key] for key in ("request_id", "mode", "tasks")}
        | {"state": "admitted", "history": receipt["history"][:1]},
    )
    gitops.commit_all(board.canonical, "simulate a receipt left behind")

    [outcome] = board.process()

    assert outcome.state == "published"
    receipt = a.receipt()
    assert receipt["handed_off"] == {"task_id": a.task_id, "run_id": a.run_id}
    assert _states(receipt) == ["admitted", "handed_off", "published"]

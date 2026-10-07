"""Handoff processing and merge (Task 3.4): real git, FileQueue, simulated workers that run in
worktrees and end with ``card finish``."""

import json
from pathlib import Path

import jsonschema
import pytest

from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.requests import load_schema
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import merge
from autoresearch.runtime.card_finish import finish
from autoresearch.runtime.merge import find_orphan_done, process_handoffs

HYPOTHESIS = {
    "statement": "Heroes attack before the creep is in kill range.",
    "prediction": "Waiting for kill range raises last hits.",
    "topics": [],
    "lever": "SK.last_hit",
    "state": "proposed",
}
HYPOTHESIS_BODY = "Line one.\n\nl2\n\nl3\n\nl4\n\nLine five.\n"
EPOCH = "e1"


class Worker:
    """One admitted card: its request, task, worktree and current run."""

    def __init__(self, board: "Board", spec: CardSpec, task_id: str):
        self.board = board
        self.spec = spec
        self.task_id = task_id
        self.worktree = board.canonical / ".worktrees" / spec.request_id
        self.run_id: str | None = None

    @property
    def request_id(self) -> str:
        return self.spec.request_id

    @property
    def store(self) -> RecordStore:
        return RecordStore(Layout(self.worktree))

    def ctx(self) -> RunContext:
        return self.board.ctx(worker=self.worktree, card_id=self.task_id, run_id=self.run_id)

    def start(self) -> str:
        """Claim the card (a new run) and create its worktree from canonical HEAD."""
        card = self.board.queue.claim_next(self.request_id)
        assert card is not None and card.task_id == self.task_id
        self.run_id = card.runs[-1].run_id
        if not self.worktree.exists():
            branch = f"card/{self.request_id}"
            gitops.git(self.board.canonical, "worktree", "add", "-b", branch, str(self.worktree))
        return self.run_id

    def write_journal(self) -> str:
        return self.store.create(
            "journal",
            {
                "iteration": 1,
                "mode": self.spec.mode,
                "card": self.task_id,
                "doctrine_version": 1,
            },
            "Did things.\n\n## Decisions\n\n- none\n",
        )

    def finish(self, outcome: str = "done", terminal: bool = True) -> dict:
        result = finish(self.ctx(), outcome)
        assert result.ok, result.problems
        if terminal:
            self.end_run(outcome)
        return result.record

    def end_run(self, outcome: str = "done") -> None:
        self.board.queue.finish(self.task_id, self.run_id, outcome)

    def handoff_path(self) -> Path:
        return self.board.ledger / "handoffs" / self.task_id / f"{self.run_id}.json"

    def handoff(self) -> dict:
        return self.board.committed(self.handoff_path())

    def receipt(self) -> dict:
        return self.board.committed(self.board.receipt_path(self.request_id))


class Board:
    """A canonical repo (with ``board.json``), a FileQueue, and helpers to admit cards."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.canonical = tmp_path / "canonical"
        self.canonical.mkdir()
        self.ledger = self.canonical / ".supervisor" / "ledger"
        self.queue = FileQueue(tmp_path / "queue")
        self.store = RecordStore(Layout(self.canonical))
        self._seed()

    def _seed(self) -> None:
        root = self.canonical
        gitops.git(root, "init", "-b", "main")
        gitops.git(root, "config", "user.email", "t@example.com")
        gitops.git(root, "config", "user.name", "T")
        (root / ".gitignore").write_text(".supervisor/runtime/\n.worktrees/\n")
        (root / "AGENTS.md").write_text("# Doctrine\n")
        (root / "strategy").mkdir()
        (root / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: farm.\n")
        self.hypothesis = self.store.create("hypothesis", dict(HYPOTHESIS), HYPOTHESIS_BODY)
        write_json(self.ledger / "board.json", {"epoch": EPOCH})
        gitops.commit_all(root, "seed")

    def ctx(self, worker=None, card_id=None, run_id=None) -> RunContext:
        return RunContext(
            hermes_home=self.tmp_path / "home",
            canonical=self.canonical,
            worker=worker,
            card_id=card_id,
            run_id=run_id,
        )

    def receipt_path(self, request_id: str) -> Path:
        return self.ledger / "requests" / f"{request_id}.json"

    def committed(self, path: Path) -> dict | None:
        content = gitops.show(self.canonical, "HEAD", path.relative_to(self.canonical).as_posix())
        return None if content is None else json.loads(content)

    def admit(self, refs=(), mode="investigation", request_id=None) -> Worker:
        """What admission (Task 3.9) does: create and assign the card, commit the receipt and
        write the runtime card file. The assignee is the request id so each worker claims
        exactly its own card."""
        spec = CardSpec(
            request_id=request_id or new_id("card_request"),
            title="T",
            mode=mode,
            why="W",
            refs=list(refs),
            created_by="planner",
            priority="P2",
        )
        task_id = self.queue.create_unassigned(
            spec,
            branch=f"card/{spec.request_id}",
            worktree=self.canonical / ".worktrees" / spec.request_id,
        )
        self.queue.assign(task_id, spec.request_id)
        receipt_file = self.receipt_path(spec.request_id)
        write_json(
            receipt_file,
            {
                "request_id": spec.request_id,
                "state": "admitted",
                "mode": mode,
                "tasks": [{"task_id": task_id, "board_epoch": EPOCH}],
                "history": [{"state": "admitted", "at": "2026-10-07T00:00:00+00:00"}],
            },
        )
        gitops.commit_paths(
            self.canonical, [receipt_file.relative_to(self.canonical).as_posix()], "admit"
        )
        write_json(
            self.canonical / ".supervisor" / "runtime" / "cards" / f"{task_id}.json",
            {
                "task_id": task_id,
                "request_id": spec.request_id,
                "mode": mode,
                "refs": list(refs),
                "finished": False,
            },
        )
        return Worker(self, spec, task_id)

    def admit_follow_up(self, request_id: str) -> Worker:
        """Admit a runtime-written repair/reconcile request committed in canonical."""
        path = self.canonical / "cards" / "requests" / f"{request_id}.json"
        request = self.committed(path)
        spec = request["spec"]
        return self.admit(refs=spec["refs"], mode=spec["mode"], request_id=request_id)

    def process(self, publish=None, queue=None):
        return process_handoffs(self.ctx(), queue or self.queue, self.store, publish=publish)

    def merge_commits(self) -> list[str]:
        return gitops.git(self.canonical, "rev-list", "--merges", "HEAD").split()

    def follow_up(self, request_id: str) -> dict:
        return self.committed(self.canonical / "cards" / "requests" / f"{request_id}.json")


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _new_hypothesis(worker: Worker) -> str:
    return worker.store.create("hypothesis", dict(HYPOTHESIS), "New.\n")


def _states(record: dict) -> list[str]:
    return [entry["state"] for entry in record["history"]]


def _assert_clean(board: Board) -> None:
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


# --- merging ---


def test_disjoint_new_entities_both_merge_in_finish_order(board):
    a, b = board.admit(), board.admit()
    a.start()
    b.start()
    a_id, b_id = _new_hypothesis(a), _new_hypothesis(b)
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
    written = sorted((board.canonical / "cards" / "requests").glob("Q-*.json"))
    assert len(written) == 1

    [outcome] = board.process()

    assert outcome.state == "repair"
    assert sorted((board.canonical / "cards" / "requests").glob("Q-*.json")) == written
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

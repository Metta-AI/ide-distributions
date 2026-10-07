"""Outbox publication (Task 3.5, Overview C5): real git, FileQueue and simulated workers.

The Board/Worker helpers come from the handoff-processing tests (``test_merge.py``)."""

import json
import shutil

import pytest
from test_merge import Board, _new_hypothesis

from autoresearch.comms import publisher
from autoresearch.comms.publisher import publish_hook, publish_runtime, publish_staged
from autoresearch.comms.reader import advance, read_since
from autoresearch.comms.resolver import ResolveError, resolve
from autoresearch.comms.stage import Ref, stage
from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.runtime.ledger import canonical_lock

CRASH_STEPS = (
    "outbox_written",
    "receipt_written",
    "staged_removed",
    "before_commit",
    "before_push",
)


class Crash(RuntimeError):
    pass


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


@pytest.fixture
def remote(board, tmp_path):
    bare = tmp_path / "remote.git"
    gitops.git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    gitops.git(board.canonical, "remote", "add", "origin", str(bare))
    gitops.git(board.canonical, "push", "origin", "main")
    return bare


def _remote_head(bare) -> str:
    return gitops.git(bare, "rev-parse", "main")


def _remote_outbox(bare) -> list[str]:
    listing = gitops.git(bare, "ls-tree", "--name-only", "main", "comms/outbox/")
    return sorted(path.rsplit("/", 1)[-1] for path in listing.splitlines())


def _outbox(board) -> list[str]:
    listing = gitops.git(board.canonical, "ls-tree", "--name-only", "HEAD", "comms/outbox/")
    return sorted(path.rsplit("/", 1)[-1] for path in listing.splitlines())


def _receipts(board) -> list[str]:
    listing = gitops.git(
        board.canonical, "ls-tree", "--name-only", "HEAD", ".supervisor/ledger/published/"
    )
    return sorted(path.rsplit("/", 1)[-1] for path in listing.splitlines())


def _seqs(board) -> list[int]:
    return [int(name.split("-", 1)[0]) for name in _outbox(board)]


def _assert_clean(board) -> None:
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


def _worker_with_message(board, title: str, count: int = 1):
    worker = board.admit()
    worker.start()
    hypothesis = _new_hypothesis(worker)
    worker.write_journal()
    ids = [
        stage(
            Layout(worker.worktree),
            "highlight",
            f"{title} {index}",
            "A new hypothesis.",
            [Ref(f"ar://hypothesis/{hypothesis}", role="subject")],
        )
        for index in range(count)
    ]
    return worker, hypothesis, ids


def test_reverse_order_publication_is_gap_free_and_readable_between(board):
    a, a_hypothesis, [a_message] = _worker_with_message(board, "A")
    b, b_hypothesis, [b_message] = _worker_with_message(board, "B")

    b.finish()
    [outcome_b] = board.process(publish=publish_hook)
    first = read_since(board.canonical, "preston")
    assert [message["id"] for message in first] == [b_message]
    advance(board.canonical, "preston", first[-1]["seq"])

    a.finish()
    [outcome_a] = board.process(publish=publish_hook)
    later = read_since(board.canonical, "preston")
    assert [message["id"] for message in later] == [a_message]

    assert _seqs(board) == [1, 2]
    assert first[0]["seq"] == 1 and later[0]["seq"] == 2
    for message, outcome, hypothesis in (
        (first[0], outcome_b, b_hypothesis),
        (later[0], outcome_a, a_hypothesis),
    ):
        assert message["published_rev"] == outcome.merge_sha
        [ref] = message["refs"]
        assert ref["rev"] == outcome.merge_sha
        assert resolve(board.canonical, ref["uri"], ref["rev"]).rev == outcome.merge_sha
    with pytest.raises(ResolveError):  # A's entity did not exist when B published
        resolve(board.canonical, f"ar://hypothesis/{a_hypothesis}", outcome_b.merge_sha)
    assert not (board.canonical / "comms" / "staged").exists() or not any(
        (board.canonical / "comms" / "staged").iterdir()
    )
    assert len(_receipts(board)) == 2
    _assert_clean(board)


def test_receipt_records_the_publication(board):
    worker, _, [message_id] = _worker_with_message(board, "A")
    worker.finish()
    [outcome] = board.process(publish=publish_hook)
    receipt = json.loads(
        (
            board.canonical / ".supervisor" / "ledger" / "published" / f"{message_id}.json"
        ).read_text()
    )
    assert receipt["id"] == message_id and receipt["seq"] == 1
    assert receipt["published_rev"] == outcome.merge_sha
    assert receipt["file"] == f"comms/outbox/00000001-{message_id}.json"


@pytest.mark.parametrize("step", CRASH_STEPS)
def test_crash_at_each_step_recovers_to_one_file_and_receipt_per_message(
    board, remote, monkeypatch, step
):
    worker, _, ids = _worker_with_message(board, "A", count=2)
    worker.finish()
    real_checkpoint = publisher._checkpoint

    def crash_once(name: str) -> None:
        if name == step:
            monkeypatch.setattr(publisher, "_checkpoint", real_checkpoint)
            raise Crash(name)

    monkeypatch.setattr(publisher, "_checkpoint", crash_once)
    with pytest.raises(Crash):
        board.process(publish=publish_hook)
    assert worker.handoff()["state"] == "merged"

    [outcome] = board.process(publish=publish_hook)

    assert outcome.state == "published"
    assert _seqs(board) == [1, 2]
    assert _outbox(board) == sorted(
        f"{seq:08d}-{message_id}.json" for seq, message_id in zip((1, 2), sorted(ids), strict=True)
    )
    assert _receipts(board) == sorted(f"{message_id}.json" for message_id in ids)
    assert gitops.git(board.canonical, "ls-tree", "HEAD", "comms/staged/") == ""
    # The publication was pushed; the handoff's own "published" commit comes after the hook
    # and is pushed by queue-keeper's end-of-run push (Task 3.9).
    assert _remote_outbox(remote) == _outbox(board)
    _assert_clean(board)


def test_recover_alone_completes_a_partial_publication(board, monkeypatch):
    worker, _, [message_id] = _worker_with_message(board, "A")
    worker.finish()

    def crash(name: str) -> None:
        if name == "outbox_written":
            raise Crash(name)

    monkeypatch.setattr(publisher, "_checkpoint", crash)
    with pytest.raises(Crash):
        board.process(publish=publish_hook)
    monkeypatch.undo()
    with canonical_lock(board.canonical):
        publisher.recover(board.canonical)
    assert _outbox(board) == [f"00000001-{message_id}.json"]
    assert _receipts(board) == [f"{message_id}.json"]
    _assert_clean(board)


def test_push_failure_keeps_the_local_commit_and_the_next_run_pushes(board, remote, tmp_path):
    worker, _, _ = _worker_with_message(board, "A")
    worker.finish()
    hidden = tmp_path / "remote-unavailable.git"
    remote.rename(hidden)
    [outcome] = board.process(publish=publish_hook)
    assert outcome.state == "published" and _seqs(board) == [1]
    hidden.rename(remote)
    assert _remote_head(remote) != gitops.head(board.canonical)

    with canonical_lock(board.canonical):
        assert publish_staged(board.canonical, gitops.head(board.canonical)) == []
    assert _remote_head(remote) == gitops.head(board.canonical)


def test_runtime_messages_share_the_sequence(board):
    worker, _, _ = _worker_with_message(board, "A")
    worker.finish()
    board.process(publish=publish_hook)
    with canonical_lock(board.canonical):
        seq = publish_runtime(
            board.canonical, "help_request", "Paused", "Identity check failed.", [Ref("ar://plan")]
        )
    assert seq == 2 and _seqs(board) == [1, 2]
    [message] = read_since(board.canonical, "other", after=1)
    assert message["from"] == "runtime"
    assert message["refs"][0]["rev"] == message["published_rev"]
    _assert_clean(board)


def test_staged_file_with_a_receipt_is_only_removed(board):
    worker, _, [message_id] = _worker_with_message(board, "A")
    worker.finish()
    board.process(publish=publish_hook)
    staged = board.canonical / "comms" / "staged" / f"{message_id}.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(board.canonical / "comms" / "outbox" / f"00000001-{message_id}.json", staged)
    gitops.commit_all(board.canonical, "a stale staged copy")
    with canonical_lock(board.canonical):
        assert publish_staged(board.canonical, gitops.head(board.canonical)) == []
    assert _seqs(board) == [1] and not staged.exists()
    _assert_clean(board)

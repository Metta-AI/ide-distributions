"""MEMORY.md snapshot into canonical (Plan 03 Task 5.2)."""

from autoresearch.io import gitops
from autoresearch.runtime.memory_snapshot import SNAPSHOT_RELATIVE, snapshot_memory


def write_memory(ctx, text):
    path = ctx.hermes_home / "memories" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_snapshot_is_copied_and_committed(ctx):
    write_memory(ctx, "remember the noise floor\n")
    assert snapshot_memory(ctx) is True
    target = ctx.canonical / SNAPSHOT_RELATIVE
    assert target.read_text() == "remember the noise floor\n"
    assert gitops.show(ctx.canonical, "HEAD", SNAPSHOT_RELATIVE) == b"remember the noise floor\n"


def test_unchanged_memory_makes_no_commit(ctx):
    write_memory(ctx, "one\n")
    snapshot_memory(ctx)
    head = gitops.head(ctx.canonical)
    assert snapshot_memory(ctx) is False
    assert gitops.head(ctx.canonical) == head


def test_changed_memory_makes_a_new_commit(ctx):
    write_memory(ctx, "one\n")
    snapshot_memory(ctx)
    head = gitops.head(ctx.canonical)
    write_memory(ctx, "two\n")
    assert snapshot_memory(ctx) is True
    assert gitops.head(ctx.canonical) != head


def test_missing_memory_file_is_a_no_op(ctx):
    head = gitops.head(ctx.canonical)
    assert snapshot_memory(ctx) is False
    assert gitops.head(ctx.canonical) == head
    assert not (ctx.canonical / SNAPSHOT_RELATIVE).exists()

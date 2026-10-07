import json
import shutil

import jsonschema
import pytest

from autoresearch import cli
from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.requests import load_schema
from autoresearch.runtime.card_finish import FinishUsageError, finish
from autoresearch.runtime.handoff import card_spec, validate_at


def _finish_cli(card, monkeypatch, outcome="done", run_id="run-1"):
    for name, value in card.env(run_id).items():
        monkeypatch.setenv(name, value)
    return cli.main(["card", "finish", "--outcome", outcome])


def _commit_count(repo):
    return int(gitops.git(repo, "rev-list", "--count", "HEAD"))


def _validate(card, base_sha, result_sha, responsible=()):
    spec = card_spec(read_json(card.card_file))
    return validate_at(card.canonical, base_sha, result_sha, spec, list(responsible), card.task_id)


# --- the happy path ---


def test_valid_finish_records_handoff_in_one_canonical_commit(card, monkeypatch, capsys):
    journal_id = card.write_journal()
    before = _commit_count(card.canonical)

    assert _finish_cli(card, monkeypatch) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    record = read_json(card.handoff_path("run-1"))
    jsonschema.validate(record, load_schema("handoff.json"))
    result_sha = gitops.head(card.worktree)
    assert record["card"] == card.task_id and record["run_id"] == "run-1"
    assert record["request_id"] == card.request_id
    assert record["base_sha"] == card.base_sha and record["result_sha"] == result_sha
    assert record["outcome"] == "done" and record["state"] == "committed"
    assert record["journal"] == f"journal/{journal_id}.md"
    assert record["refs"] == card.refs and record["report"]["ok"] is True
    assert record["report"]["created"] == [journal_id]
    assert output["data"] == record
    # The worktree change was committed on the card branch.
    assert gitops.git(card.worktree, "status", "--porcelain") == ""
    assert gitops.git(card.worktree, "log", "-1", "--format=%s") == (
        f"card {card.task_id} run run-1: done"
    )
    # One canonical commit holds both the record and the receipt move.
    assert _commit_count(card.canonical) == before + 1
    changed = gitops.git(card.canonical, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(changed) == sorted(
        [
            f".supervisor/ledger/handoffs/{card.task_id}/run-1.json",
            f".supervisor/ledger/requests/{card.request_id}.json",
        ]
    )
    receipt = read_json(card.receipt_path)
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "handed_off"
    assert receipt["handed_off"] == {"task_id": card.task_id, "run_id": "run-1"}
    assert [entry["state"] for entry in receipt["history"]] == ["admitted", "handed_off"]
    assert read_json(card.card_file)["finished"] is True


def test_no_uncommitted_changes_records_an_empty_commit(card):
    card.write_journal()
    gitops.commit_all(card.worktree, "worker commit")
    worker_head = gitops.head(card.worktree)

    result = finish(card.ctx(), "done")

    assert result.ok
    result_sha = result.record["result_sha"]
    assert result_sha != worker_head
    assert gitops.git(card.worktree, "rev-parse", f"{result_sha}^") == worker_head
    assert gitops.git(card.worktree, "rev-parse", f"{result_sha}^{{tree}}") == gitops.git(
        card.worktree, "rev-parse", f"{worker_head}^{{tree}}"
    )
    assert card.handoff_path("run-1").exists()


# --- invalid content ---


def test_missing_journal_exits_1_and_writes_no_record(card, monkeypatch, capsys):
    before = _commit_count(card.canonical)

    assert _finish_cli(card, monkeypatch) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert any("journal" in problem for problem in output["error"]["problems"])
    assert not card.handoff_path("run-1").exists()
    assert _commit_count(card.canonical) == before
    assert read_json(card.receipt_path)["state"] == "admitted"
    assert read_json(card.card_file)["finished"] is False


def test_failed_outcome_records_even_invalid_content(card):
    result = finish(card.ctx(), "failed")

    assert result.ok
    record = read_json(card.handoff_path("run-1"))
    assert record["outcome"] == "failed" and record["report"]["ok"] is False
    assert record["journal"] is None
    assert read_json(card.card_file)["finished"] is True


def test_journal_for_another_card_does_not_count(card):
    card.write_journal(card="K-other")
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("exactly one new journal" in problem for problem in result.problems)


def test_journal_without_decisions_section_is_a_problem(card):
    journal_id = new_id("journal")
    card.write_worker_doc(
        f"journal/{journal_id}.md",
        {"id": journal_id, "iteration": 1, "mode": "investigation", "card": card.task_id},
        "No decisions here.\n",
    )
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("## Decisions" in problem for problem in result.problems)
    assert any("doctrine_version" in problem for problem in result.problems)


def test_editing_existing_hypothesis_not_in_refs_is_d23(card):
    card.write_journal()
    card.worker_store.update(card.not_in_refs, {"statement": "Changed statement."})

    result = finish(card.ctx(), "done")

    assert not result.ok
    assert result.report.violations == {"D-23": 1}
    assert any(card.not_in_refs in problem for problem in result.problems)
    assert result.report.changed_existing == [card.not_in_refs]
    assert not card.handoff_path("run-1").exists()


def test_editing_existing_hypothesis_in_refs_is_allowed(card):
    card.write_journal()
    card.worker_store.update(card.in_refs, {"statement": "Changed statement."})

    result = finish(card.ctx(), "done")

    assert result.ok, result.problems
    assert result.report.changed_existing == [card.in_refs]
    assert result.report.violations == {}


def test_deleting_a_record_is_a_problem(card):
    card.write_journal()
    card.worker_store.path_of(card.in_refs).unlink()
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("deleted" in problem for problem in result.problems)


def test_invalid_record_change_is_a_problem(card):
    card.write_journal()
    path = card.worker_store.path_of(card.in_refs)
    path.write_text(path.read_text().replace("state: proposed", "state: supported"))
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("transition proposed -> supported" in problem for problem in result.problems)


@pytest.mark.parametrize(
    "rel_path",
    [
        "kb/index.md",
        "kb/_generated/claims.md",
        ".supervisor/ledger/promotion.json",
        "comms/inbox/I-1.json",
    ],
)
def test_editing_generated_or_ledger_files_is_a_problem(card, rel_path):
    card.write_journal()
    target = card.worktree / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("edited\n")

    result = finish(card.ctx(), "done")

    assert not result.ok
    assert any(rel_path in problem for problem in result.problems)


def test_deleting_an_outbox_message_is_a_problem(card):
    card.write_journal()
    (outbox_file,) = (card.worktree / "comms" / "outbox").iterdir()
    outbox_file.unlink()
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("comms/outbox/" in problem for problem in result.problems)


def test_staged_messages_may_be_added_and_deleted(card):
    card.write_journal()
    staged = card.stage_message()
    gitops.commit_all(card.worktree, "stage")
    card.stage_message()
    staged.unlink()
    result = finish(card.ctx(), "done")
    assert result.ok, result.problems


def test_invalid_staged_message_is_a_problem(card):
    card.write_journal()
    path = card.stage_message(title=None, in_reply_to=7)
    message = read_json(path)
    del message["title"]
    write_json(path, message)
    (card.worktree / "comms" / "staged" / "broken.json").write_text("{not json")

    result = finish(card.ctx(), "done")

    assert not result.ok
    assert any("title" in problem for problem in result.problems)
    assert any("in_reply_to" in problem for problem in result.problems)
    assert any("broken.json" in problem for problem in result.problems)


def test_invalid_card_request_is_a_problem(card):
    card.write_journal()
    request_id = new_id("card_request")
    write_json(card.worktree / "cards" / "requests" / f"{request_id}.json", {"id": request_id})
    result = finish(card.ctx(), "done")
    assert not result.ok
    assert any("cards/requests" in problem for problem in result.problems)


# --- responsible inbound ---


def _set_responsible(card, inbound_ids):
    meta = read_json(card.card_file)
    meta["responsible_inbound"] = inbound_ids
    write_json(card.card_file, meta)


def test_unacknowledged_responsible_inbound_is_named(card):
    inbound = new_id("inbound")
    card.write_inbound(inbound, "reply")
    _set_responsible(card, [inbound])
    card.write_journal()

    result = finish(card.ctx(), "done")

    assert not result.ok
    assert any(inbound in problem for problem in result.problems)


def test_acknowledged_responsible_inbound_passes_and_is_recorded(card):
    inbound = new_id("inbound")
    card.write_inbound(inbound, "steer")
    _set_responsible(card, [inbound])
    card.write_journal()
    card.stage_message(in_reply_to=inbound)

    result = finish(card.ctx(), "done")

    assert result.ok, result.problems
    assert result.record["responsible_inbound"] == [inbound]


def test_non_responsible_inbound_need_not_be_acknowledged(card):
    card.write_inbound(new_id("inbound"), "reply")
    card.write_journal()
    assert finish(card.ctx(), "done").ok


def test_task_inbound_is_exempt(card):
    inbound = new_id("inbound")
    card.write_inbound(inbound, "task")
    _set_responsible(card, [inbound])
    card.write_journal()
    assert finish(card.ctx(), "done").ok


# --- retries, idempotency, durability ---


def test_retry_with_new_run_id_writes_second_record(card):
    card.write_journal()
    assert finish(card.ctx("run-1"), "partial").ok
    first = card.handoff_path("run-1").read_bytes()
    meta = read_json(card.card_file)
    write_json(card.card_file, meta | {"finished": False})  # the runtime reopens the card

    result = finish(card.ctx("run-2"), "done")

    assert not result.ok  # the journal from run-1 is already committed on the branch
    card.write_journal()
    result = finish(card.ctx("run-2"), "done")
    assert result.ok, result.problems
    assert card.handoff_path("run-1").read_bytes() == first
    second = read_json(card.handoff_path("run-2"))
    assert second["run_id"] == "run-2" and second["base_sha"] == card.base_sha
    receipt = read_json(card.receipt_path)
    assert receipt["handed_off"] == {"task_id": card.task_id, "run_id": "run-2"}
    assert [entry["state"] for entry in receipt["history"]] == [
        "admitted",
        "handed_off",
        "handed_off",
    ]


def test_rerun_with_same_run_id_returns_existing_record(card):
    card.write_journal()
    first = finish(card.ctx(), "done")
    canonical_head, worker_head = gitops.head(card.canonical), gitops.head(card.worktree)
    meta = read_json(card.card_file)
    write_json(card.card_file, meta | {"finished": False})  # crash before finished=true

    again = finish(card.ctx(), "done")

    assert again.ok and again.existing
    assert again.record == first.record
    assert gitops.head(card.canonical) == canonical_head
    assert gitops.head(card.worktree) == worker_head
    assert read_json(card.card_file)["finished"] is True


def test_validation_works_from_shas_after_worktree_is_deleted(card):
    card.write_journal()
    card.worker_store.update(card.in_refs, {"statement": "Changed statement."})
    record = finish(card.ctx(), "done").record

    gitops.git(card.canonical, "worktree", "remove", "--force", str(card.worktree))
    shutil.rmtree(card.worktree, ignore_errors=True)
    assert not card.worktree.exists()

    report = _validate(card, record["base_sha"], record["result_sha"])
    assert report.ok, report.problems
    assert report.to_dict() == record["report"]


# --- usage errors ---


def test_finish_requires_worker_card_and_run(card):
    ctx = card.ctx()
    for missing in ("worker", "card_id", "run_id"):
        with pytest.raises(FinishUsageError):
            finish(type(ctx)(**{**ctx.__dict__, missing: None}), "done")
    with pytest.raises(FinishUsageError):
        finish(ctx, "succeeded")


def test_cli_without_card_context_exits_2(card, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(card.tmp_path / "home"))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(card.canonical))
    for name in ("HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["card", "finish", "--outcome", "done"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_rejects_unknown_outcome(card, monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        _finish_cli(card, monkeypatch, outcome="succeeded")
    assert exit_info.value.code == 2

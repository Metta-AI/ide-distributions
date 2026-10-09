"""Canonical commit provenance (``runtime.provenance``, C1): queue-keeper detects a commit on
canonical ``main`` that no runtime writer recorded, pauses once and asks a human, and only
the operator command ``runtime accept-commits`` clears it. The forged commit below is the
2026-10-09 incident: a card worker took ``canonical.lock`` from a Python script, wrote an
``admitted`` receipt and committed it on ``main`` under the runtime's git identity."""

import ast
import datetime
import json
from pathlib import Path

import pytest
from support.keeper_host import PROFILE, Host

from autoresearch.cli import main
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.io.locks import file_lock
from autoresearch.runtime import provenance

T0 = datetime.datetime(2026, 10, 9, 0, 0, tzinfo=datetime.UTC)
FORGED_REQUEST = "Q-01M4F299EGSMT20DGKFKYS1QZV"
SRC = Path(provenance.__file__).resolve().parents[1]


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    host = Host(tmp_path)
    first = host.tick(at(0))
    assert first.provenance_violation is None and not first.paused
    return host


def _provenance_messages(host: Host) -> list[dict]:
    return [m for m in host.outbox() if str(m.get("runtime_key", "")).startswith("provenance")]


def _forge(host: Host) -> str:
    """What the worker's ``python3 - <<'PYEOF'`` script did: take the canonical lock, write
    an admitted receipt, and commit it on main."""
    lock = host.canonical / ".supervisor" / "runtime" / "canonical.lock"
    receipt = host.ledger / "requests" / f"{FORGED_REQUEST}.json"
    with file_lock(lock):
        write_json(receipt, {"request_id": FORGED_REQUEST, "state": "admitted", "tasks": []})
        gitops.git(host.canonical, "add", str(receipt))
        gitops.git(
            host.canonical, "-c", "user.name=Autoresearch shim", "-c", "user.email=shim@x",
            "commit", "-m", f"admit {FORGED_REQUEST} (manual admission repair)",
        )
    return host.head()


def _cli(
    monkeypatch, capsys, host: Host, *argv: str, card: bool = False, agent: bool = False
) -> tuple[int, dict]:
    monkeypatch.setenv("HERMES_HOME", str(host.home))
    monkeypatch.delenv("AUTORESEARCH_WORKSPACE", raising=False)
    if agent:
        monkeypatch.setenv("AUTORESEARCH_AGENT_SESSION", "1")
    else:
        monkeypatch.delenv("AUTORESEARCH_AGENT_SESSION", raising=False)
    if card:
        monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(host.canonical / ".worktrees" / "x"))
    else:
        monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    code = main(["runtime", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_a_forged_commit_pauses_the_agent_with_one_help_request(host):
    forged = _forge(host)

    run = host.tick(at(1))
    violation = run.provenance_violation
    assert violation is not None and violation.kind == provenance.UNKNOWN_COMMITS
    assert [commit.sha for commit in violation.commits] == [forged]
    assert violation.commits[0].author == "Autoresearch shim <shim@x>"
    assert violation.commits[0].files == [f".supervisor/ledger/requests/{FORGED_REQUEST}.json"]
    assert run.paused and run.preflight is None  # it stopped before acting
    assert run.handoffs == [] and run.admitted == []
    pause = json.loads((host.ledger / "pause.json").read_text())
    assert pause["reason"].startswith(provenance.PAUSE_REASON)

    [message] = _provenance_messages(host)
    assert message["kind"] == "help_request" and message["priority"] == "P0"
    assert message["about"]["commits"] == forged
    assert message["about"]["accept_commit"] == forged
    assert "by Autoresearch shim <shim@x> at " in message["body"]
    assert ".supervisor/ledger/requests/the request.json" in message["body"]
    assert "runtime accept-commits" in message["body"]
    assert "runtime check-commits" in message["body"]

    again = host.tick(at(2))
    assert again.paused and again.provenance_violation.commits[0].sha == forged
    assert len(_provenance_messages(host)) == 1  # one per set of unrecorded commits


def test_runtime_commits_pass_through_a_full_card_cycle(host):
    host.tick(at(1))  # admits the planning request
    planner = host.claim()
    planner.request("Investigate last hits", "investigation")
    planner.stage_note("Plan: last hits first")
    planner.finish()  # card finish commits the handoff on canonical (a worker process)

    for minute in (2, 3, 4):
        run = host.tick(at(minute))
        assert run.provenance_violation is None and "provenance" not in run.failures
    assert [h.state for h in host.tick(at(5)).handoffs] == []  # already published
    assert not (host.ledger / "pause.json").exists()
    entries, invalid = provenance._entries(host.canonical)
    assert invalid == 0
    checked = entries[provenance._base(entries)]
    assert checked["kind"] == provenance.VERIFIED and checked["sha"] == host.head()


def _checked(host: Host) -> str:
    entries, _ = provenance._entries(host.canonical)
    return entries[provenance._base(entries)]["sha"]


@pytest.mark.parametrize("below_checked", [False, True])
def test_rewritten_history_is_detected(host, below_checked):
    """A reset that drops commits made since the last check (they were recorded), and one
    that drops the last checked commit itself."""
    host.tick(at(1))  # admits the planning request: commits after the checked point
    checked = _checked(host)
    assert host.head() != checked
    target = f"{checked}~1" if below_checked else "HEAD~1"
    gitops.git(host.canonical, "reset", "--hard", "-q", target)

    run = host.tick(at(2))
    violation = run.provenance_violation
    assert violation.kind == provenance.REWRITTEN
    assert violation.base == checked and violation.dropped >= 1 and violation.commits == []
    assert run.paused and run.preflight is None
    [message] = _provenance_messages(host)
    assert message["title"] == "Agent paused: the workspace history was rewritten"
    assert message["about"]["last_checked"] == checked
    assert host.tick(at(3)).paused and len(_provenance_messages(host)) == 1


def test_accept_commits_clears_the_pause(host, monkeypatch, capsys):
    forged = _forge(host)
    assert host.tick(at(1)).paused

    code, envelope = _cli(monkeypatch, capsys, host, "check-commits")
    assert code == 0 and envelope["data"]["violation"]["accept_sha"] == forged
    assert envelope["data"]["invalid_journal_lines"] == 0
    assert host.tick(at(2)).paused  # check-commits changes nothing

    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged[:12])
    assert code == 0 and envelope["data"]["accepted"] == forged

    run = host.tick(at(3))
    assert run.provenance_violation is None and not run.paused
    assert not (host.ledger / "pause.json").exists()
    [resumed] = [m for m in host.outbox() if m["title"] == "Agent resumed"]
    assert "a person accepted" in resumed["body"]
    # The accept itself is announced once, so a forged accept is never silent (review R2-M2).
    [accepted] = [m for m in host.outbox() if m["title"] == "Workspace commits accepted"]
    assert accepted["kind"] == "note" and accepted["about"]["accepted_commit"] == forged
    assert "If you did not run accept-commits" in accepted["body"]
    host.tick(at(4))
    assert len([m for m in host.outbox() if m["title"] == "Workspace commits accepted"]) == 1
    code, envelope = _cli(monkeypatch, capsys, host, "check-commits")
    assert code == 0 and envelope["data"]["violation"] is None


def test_runtime_session_reports_the_agent_session_marker(host, monkeypatch, capsys):
    """Review R2-M5: the deploy check that agent terminals carry the plugin's marker."""
    code, envelope = _cli(monkeypatch, capsys, host, "session", agent=True)
    assert code == 0 and envelope["data"] == {"agent_session": True, "card": None}
    code, envelope = _cli(monkeypatch, capsys, host, "session")
    assert code == 0 and envelope["data"]["agent_session"] is False


def test_accept_commits_is_refused_in_a_card_and_for_foreign_commits(host, monkeypatch, capsys):
    forged = _forge(host)
    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged, card=True)
    assert code == 1 and "human operator" in envelope["error"]["message"]
    # Any agent session (a planner or chat session too): the plugin's marker, however the
    # command word was assembled.
    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged, agent=True)
    assert code == 1 and "agent session" in envelope["error"]["message"]
    assert host.tick(at(1)).paused

    other = host.tmp_path / "other"
    gitops.git(host.tmp_path, "init", "-q", "-b", "main", str(other))
    gitops.git(other, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q",
               "--allow-empty", "-m", "elsewhere")  # fmt: skip
    gitops.git(host.canonical, "fetch", "-q", str(other), "main")
    stray = gitops.git(host.canonical, "rev-parse", "FETCH_HEAD")
    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", stray)
    assert code == 1 and "not in the history" in envelope["error"]["message"]


def test_a_lost_journal_is_reported_not_trusted_again(host, monkeypatch, capsys):
    (provenance.journal_dir(host.canonical) / provenance.KEY_FILE).unlink()

    run = host.tick(at(1))
    assert run.provenance_violation.kind == provenance.JOURNAL_LOST
    [message] = _provenance_messages(host)
    assert message["runtime_key"] == "provenance-journal:2026-10-09"

    code, _ = _cli(monkeypatch, capsys, host, "accept-commits", host.head())
    assert code == 0
    assert not host.tick(at(2)).paused


def test_an_entry_forged_without_the_key_does_not_count(host):
    forged = _forge(host)
    entry = {"kind": "commit", "sha": forged, "parent": "x", "step": "s", "mac": "0" * 64}
    with provenance.journal_path(host.canonical).open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    assert host.tick(at(1)).provenance_violation.commits[0].sha == forged


def test_bootstrap_trusts_a_workspace_from_before_the_check_once(tmp_path):
    """The first run with this check on a workspace that never had a journal (an upgrade)
    trusts HEAD once and commits the marker; a journal lost after that is not trusted again."""
    repo = tmp_path / "canonical"
    gitops.git(tmp_path, "init", "-q", "-b", "main", str(repo))
    gitops.git(repo, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q",
               "--allow-empty", "-m", "an install from before the check")  # fmt: skip
    gitops.git(repo, "config", "user.name", "x")
    gitops.git(repo, "config", "user.email", "x@x")

    action = provenance.initialize(repo, fresh=False)
    assert action and "trusted HEAD" in action
    assert gitops.show(repo, "HEAD", provenance.MARKER_RELATIVE) is not None
    assert provenance.check(repo) is None

    for path in provenance.journal_dir(repo).iterdir():
        path.unlink()
    assert provenance.initialize(repo, fresh=False) is None
    assert provenance.check(repo).kind == provenance.JOURNAL_LOST


def test_deleting_the_journal_and_the_marker_is_still_reported(host):
    """Removing the marker in a later commit does not make a lost journal trusted again: the
    marker was once in history (I-6)."""
    gitops.git(host.canonical, "rm", "-q", provenance.MARKER_RELATIVE)
    gitops.git(host.canonical, "commit", "-q", "-m", "drop the marker")
    for path in provenance.journal_dir(host.canonical).iterdir():
        path.unlink()

    run = host.tick(at(1))
    assert run.provenance_violation.kind == provenance.JOURNAL_LOST
    assert not any("provenance journal initialized" in a for a in run.bootstrap.actions)


def _prepare_plan(host: Host, text: str, message: str = "a runtime step"):
    (host.canonical / "plan.md").write_text(text)
    return gitops.prepare_paths(host.canonical, ["plan.md"], message)


def _commit_plan_outside_the_runtime(host: Host, text: str) -> str:
    (host.canonical / "plan.md").write_text(text)
    return gitops.commit_all(host.canonical, "a runtime step")


def test_a_crash_between_commit_and_record_is_recognized(host, monkeypatch):
    """M-1: the intent entry, written before HEAD moves, lets the check recognize a runtime
    commit whose commit entry was never written (a kill in between)."""
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt  # the process dies before the commit entry

    monkeypatch.setattr(provenance, "record", crash)
    with pytest.raises(KeyboardInterrupt):
        provenance.commit_recorded(
            host.canonical, "a runtime step", lambda: _prepare_plan(host, "crash\n")
        )
    monkeypatch.undo()
    assert host.tick(at(1)).provenance_violation is None

    # The same subject on the same parent, made outside the runtime, is not the runtime's.
    _commit_plan_outside_the_runtime(host, "forged\n")
    assert host.tick(at(2)).provenance_violation.kind == provenance.UNKNOWN_COMMITS


def test_a_writer_that_fails_before_committing_vouches_for_nothing(host):
    """Codex review of PR #24, F1: a runtime writer that fails before it commits must not
    leave an intent that a later commit on the same parent with the same subject matches."""
    def fails():
        raise gitops.GitError(("commit",), "index.lock exists")

    with pytest.raises(gitops.GitError):
        provenance.commit_recorded(host.canonical, "a runtime step", fails)
    forged = _commit_plan_outside_the_runtime(host, "forged\n")
    violation = host.tick(at(1)).provenance_violation
    assert violation.kind == provenance.UNKNOWN_COMMITS
    assert [commit.sha for commit in violation.commits] == [forged]


def test_an_open_intent_vouches_only_for_its_exact_commit(host, monkeypatch):
    """F1: a crash after the intent but before HEAD moved leaves an open intent; it names
    the prepared commit, so a different commit with the same parent and subject is still
    reported."""
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(gitops, "land", crash)
    with pytest.raises(KeyboardInterrupt):
        provenance.commit_recorded(
            host.canonical, "a runtime step", lambda: _prepare_plan(host, "runtime\n")
        )
    monkeypatch.undo()
    [intent] = [e for e in provenance._entries(host.canonical)[0] if e["kind"] == "intent"
                and e["step"] == "a runtime step"]  # fmt: skip
    forged = _commit_plan_outside_the_runtime(host, "forged\n")
    assert forged != intent["sha"] and intent["parent"] == gitops.git(
        host.canonical, "rev-parse", f"{forged}^"
    )
    violation = host.tick(at(1)).provenance_violation
    assert [commit.sha for commit in violation.commits] == [forged]


def test_a_refused_branch_update_closes_its_intent(host):
    """F1: when ``main`` moved after the commit was prepared, the update is refused, HEAD
    stays, and the intent is closed with a ``void`` entry."""
    def prepare_then_race():
        prepared = _prepare_plan(host, "runtime\n")
        _commit_plan_outside_the_runtime(host, "raced\n")
        return prepared

    with pytest.raises(gitops.GitError):
        provenance.commit_recorded(host.canonical, "a runtime step", prepare_then_race)
    entries, _ = provenance._entries(host.canonical)
    assert entries[-1]["kind"] == provenance.VOID
    assert entries[-1]["intent"] == entries[-2]["intent"] and entries[-2]["kind"] == "intent"
    assert host.head() != entries[-2]["sha"]


def test_an_unreadable_journal_line_fails_closed(host):
    """I-1: a byte that is not UTF-8 is an invalid line, not a crash of the check."""
    forged = _forge(host)
    with provenance.journal_path(host.canonical).open("ab") as handle:
        handle.write(b"\xff\xfe not json\n")
    run = host.tick(at(1))
    assert run.provenance_violation.commits[0].sha == forged
    assert provenance.invalid_lines(host.canonical) == 1


def test_a_check_that_cannot_run_pauses_and_never_resumes(host, monkeypatch):
    """I-1: a provenance step that raises fails closed: pause, nothing merged or admitted, and
    a provenance pause is not lifted by that run."""
    def broken(*_args, **_kwargs):
        raise PermissionError("journal unreadable")

    monkeypatch.setattr(provenance, "check", broken)
    run = host.tick(at(1))
    assert "provenance" in run.failures and run.paused and run.preflight is None
    assert run.admitted == [] and run.resumed is None
    pause = json.loads((host.ledger / "pause.json").read_text())
    assert pause["reason"].startswith(provenance.PAUSE_REASON)
    assert host.tick(at(2)).paused

    monkeypatch.undo()
    resumed = host.tick(at(3))
    assert not resumed.paused and not (host.ledger / "pause.json").exists()


GIT_WRITES = {"commit", "commit-tree", "update-ref", "merge", "reset", "rebase",
              "cherry-pick", "revert", "am", "pull"}  # fmt: skip


def _commits(call: ast.Call) -> bool:
    """A call that can move a branch: ``gitops.commit_*`` / ``land``, or
    ``gitops.git(repo, "<write subcommand>", ...)``."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id == "gitops"):  # fmt: skip
        return False
    if func.attr in {"commit_all", "commit_paths", "land"}:
        return True
    first = call.args[1] if func.attr == "git" and len(call.args) > 1 else None
    return isinstance(first, ast.Constant) and first.value in GIT_WRITES


def _records(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):  # inside provenance.py itself
        return func.id == "commit_recorded"
    return (isinstance(func, ast.Attribute) and func.attr == "commit_recorded"
            and isinstance(func.value, ast.Name) and func.value.id == "provenance")  # fmt: skip


def test_every_canonical_commit_records_its_provenance():
    """A new canonical commit site that forgets ``provenance.commit_recorded`` would pause
    the agent on its first commit. Every function that can move a branch either commits
    through ``commit_recorded`` or works only on a card worktree (listed, and checked to
    exist). ``merge --abort`` in ``abort_stale_merge`` moves nothing; ``commit_recorded``
    is the recorder itself."""
    worktree_only = {
        ("runtime/card_finish.py", "finish"),
        ("softmax_tool/submit.py", "bootstrap_champion"),
        ("runtime/queue_keeper.py", "abort_stale_merge"),
        ("runtime/provenance.py", "commit_recorded"),
    }
    found, missing = set(), []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative == "io/gitops.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        # A nested function handed to commit_recorded by its enclosing function is covered.
        covered = {
            id(inner)
            for outer in functions
            if any(_records(c) for c in ast.walk(outer) if isinstance(c, ast.Call))
            for inner in ast.walk(outer)
            if isinstance(inner, ast.FunctionDef) and inner is not outer
        }
        for function in functions:
            if id(function) in covered:
                continue
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
            if not any(_commits(call) for call in calls):
                continue
            found.add((relative, function.name))
            if (relative, function.name) not in worktree_only and not any(map(_records, calls)):
                missing.append((relative, function.name))
    assert missing == []
    assert worktree_only <= found  # keep the exemptions honest


def test_a_torn_last_line_does_not_swallow_the_accept(host, monkeypatch, capsys):
    """Codex review of PR #24, F3: a writer killed mid-line leaves no newline; the next
    entry (here the operator's accept) must still read back, so the pause clears."""
    forged = _forge(host)
    assert host.tick(at(1)).paused
    with provenance.journal_path(host.canonical).open("ab") as handle:
        handle.write(b'{"kind": "commit", "sha": "torn')  # no newline: the write was cut

    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged)
    assert code == 0 and envelope["data"]["accepted"] == forged
    assert provenance.invalid_lines(host.canonical) == 1  # the torn line, alone
    assert not host.tick(at(2)).paused


def test_an_accept_that_does_not_read_back_fails_loudly(host, monkeypatch, capsys):
    """F3: accept-commits verifies its anchor reads back before it reports success."""
    forged = _forge(host)
    real = provenance._entries
    monkeypatch.setattr(provenance, "_entries", lambda canonical: (real(canonical)[0][:-1], 1))
    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged)
    assert code == 1 and "does not read back" in envelope["error"]["message"]


def test_a_short_write_still_appends_the_whole_entry(host, monkeypatch):
    """F3: ``os.write`` may write fewer bytes than asked; the append continues."""
    real_write = provenance.os.write

    def short_write(descriptor, data):
        return real_write(descriptor, bytes(data[:7]))

    with monkeypatch.context() as patch:
        patch.setattr(provenance.os, "write", short_write)
        written = provenance._append(host.canonical, {"kind": "verified", "sha": host.head(),
                                                      "at": "now"})  # fmt: skip
    entries, invalid = provenance._entries(host.canonical)
    assert entries[-1] == written and invalid == 0


def test_an_unreadable_journal_pauses_and_keeps_the_spend_caps(host):
    """Codex review of PR #24, F2: a journal key bootstrap cannot read must not stop the run
    before the fail-closed pause: pending cards are unassigned (even though the pause commit
    itself cannot be recorded) and the spend step still runs. Fixing it resumes."""
    host.tick(at(1))  # admits the planning request: a pending card assigned to the profile
    assert [card.assignee for card in host.queue.list("pending")] == [PROFILE]
    key = provenance.journal_dir(host.canonical) / provenance.KEY_FILE
    key.chmod(0)
    try:
        run = host.tick(at(2))
        assert run.bootstrap.ok and "provenance" in run.failures
        assert run.paused and run.preflight is None and run.admitted == []
        assert run.spend_run is not None  # the caps still apply to running cards
        assert [card.assignee for card in host.queue.list("pending")] == [None]
        pause = json.loads((host.ledger / "pause.json").read_text())
        assert pause["reason"].startswith(provenance.PAUSE_REASON)
        assert host.tick(at(3)).paused
    finally:
        key.chmod(0o600)
    resumed = host.tick(at(4))
    assert not resumed.paused and not (host.ledger / "pause.json").exists()
    assert [card.assignee for card in host.queue.list("pending")] == [PROFILE]


def test_a_replayed_anchor_does_not_hide_a_reset(host):
    """Codex review of PR #24 round 2, H2: reset main to an old anchored commit and append
    that anchor's signed line again (no key needed). The replay has an old ``seq``, so it
    vouches for nothing and the reset is reported."""
    journal = provenance.journal_path(host.canonical)
    [anchor_line] = [line for line in journal.read_bytes().splitlines()
                     if json.loads(line)["kind"] == provenance.ANCHOR]  # fmt: skip
    anchored = json.loads(anchor_line)["sha"]
    host.tick(at(1))  # admits the planning request: recorded commits after the anchor
    host.tick(at(2))
    assert host.head() != anchored
    gitops.git(host.canonical, "reset", "--hard", "-q", anchored)
    with journal.open("ab") as handle:
        handle.write(anchor_line + b"\n")

    run = host.tick(at(3))
    assert run.provenance_violation is not None
    assert run.provenance_violation.kind == provenance.REWRITTEN
    assert provenance.invalid_lines(host.canonical) == 1


def test_a_violation_is_never_pushed_until_it_is_accepted(host, monkeypatch, capsys):
    """Codex review of PR #24 round 2, H3: publishing the violation warning (or anything
    else) must not push the unrecorded commit to origin. The warning still lands in the
    local outbox, which the bridge reads. accept-commits lets the next push through."""
    host.tick(at(1))
    pushed = host.remote_head()
    assert pushed == host.head()
    forged = _forge(host)

    assert host.tick(at(2)).paused
    assert host.remote_head() == pushed
    [warning] = _provenance_messages(host)  # in the local outbox
    assert warning["kind"] == "help_request"
    assert provenance.push_hold(host.canonical) is not None
    assert not provenance.push_main(host.canonical, "origin", ["HEAD:refs/heads/main"])
    assert host.remote_head() == pushed

    code, _ = _cli(monkeypatch, capsys, host, "accept-commits", forged)
    assert code == 0
    host.tick(at(3))
    assert host.remote_head() == host.head()
    assert gitops.git(host.canonical, "merge-base", "--is-ancestor", forged, "HEAD") == ""


def _pushes_main(call: ast.Call) -> bool:
    """``gitops.push`` / ``push_with_rejections`` with a refspec that can name main."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr in {"push", "push_with_rejections"}
            and isinstance(func.value, ast.Name) and func.value.id == "gitops"):  # fmt: skip
        return False
    refspecs = ast.unparse(call.args[2]) if len(call.args) > 2 else ""
    return any(word in refspecs for word in ("main", "MAIN", "BRANCH", "HEAD"))


def test_every_push_of_main_goes_through_the_provenance_gate():
    """H3: a new push of main that skips ``provenance.push_main`` would publish unaccepted
    commits."""
    found = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative in {"io/gitops.py", "runtime/provenance.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [
            (relative, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _pushes_main(node)
        ]
    assert found == []


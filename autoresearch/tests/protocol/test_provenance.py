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
from support.keeper_host import Host

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


def _cli(monkeypatch, capsys, host: Host, *argv: str, card: bool = False) -> tuple[int, dict]:
    monkeypatch.setenv("HERMES_HOME", str(host.home))
    monkeypatch.delenv("AUTORESEARCH_WORKSPACE", raising=False)
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
    assert entries[-1] == {**entries[-1], "kind": provenance.VERIFIED, "sha": host.head()}


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
    assert code == 0 and envelope["data"]["accept_sha"] == forged
    assert host.tick(at(2)).paused  # check-commits changes nothing

    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged[:12])
    assert code == 0 and envelope["data"]["accepted"] == forged

    run = host.tick(at(3))
    assert run.provenance_violation is None and not run.paused
    assert not (host.ledger / "pause.json").exists()
    [resumed] = [m for m in host.outbox() if m["title"] == "Agent resumed"]
    assert "a person accepted" in resumed["body"]
    code, envelope = _cli(monkeypatch, capsys, host, "check-commits")
    assert code == 0 and envelope["data"] is None


def test_accept_commits_is_refused_in_a_card_and_for_foreign_commits(host, monkeypatch, capsys):
    forged = _forge(host)
    code, envelope = _cli(monkeypatch, capsys, host, "accept-commits", forged, card=True)
    assert code == 1 and "human operator" in envelope["error"]["message"]
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


def test_bootstrap_trusts_an_existing_workspace_once(host):
    """The first run with this check on a workspace without a journal (an upgrade) trusts
    HEAD once and commits the marker; a journal lost after that is not trusted again."""
    marker = host.canonical / provenance.MARKER_RELATIVE
    gitops.git(host.canonical, "rm", "-q", str(marker))
    gitops.git(host.canonical, "commit", "-q", "-m", "an install from before the check")
    journal = provenance.journal_dir(host.canonical)
    for path in journal.iterdir():
        path.unlink()

    run = host.tick(at(1))
    assert run.provenance_violation is None
    assert any("provenance journal initialized" in a for a in run.bootstrap.actions)
    assert json.loads(marker.read_text())["trusted_head"]


def test_every_canonical_commit_records_its_provenance():
    """A new canonical commit site that forgets ``provenance.record`` would pause the agent
    on its first commit. Every function that commits (``gitops.commit_*``, ``merge_no_ff``)
    either records it or commits only in a card worktree."""
    committers = {"commit_all", "commit_paths", "commit_blobs", "merge_no_ff"}
    worktree_only = {
        ("runtime/card_finish.py", "finish"),
        ("softmax_tool/submit.py", "bootstrap_champion"),
    }
    found, missing = set(), []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative == "io/gitops.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            calls = {
                getattr(node.func, "attr", getattr(node.func, "id", None))
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
            }
            if not calls & committers:
                continue
            found.add((relative, function.name))
            if (relative, function.name) not in worktree_only and "record" not in calls:
                missing.append((relative, function.name))
    assert missing == []
    assert worktree_only <= found  # keep the exemptions honest

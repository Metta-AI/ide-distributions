import subprocess

import pytest

from orchestrator.artifacts import Reports
from orchestrator.board import Board


@pytest.fixture
def report(context, monkeypatch):
    workspace = context.workspace
    def git(*args):
        return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()
    git("init", "-b", "card/test")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    (workspace / "report.md").write_text("Evidence 🎯\n" * 1000)
    (workspace / "link.md").symlink_to("/etc/passwd")
    git("add", ".")
    git("commit", "-m", "report")
    commit = git("rev-parse", "HEAD")
    snapshot = {
        "card": {"id": "t_test", "status": "done", "workspace_path": str(workspace), "branch_name": "card/test"},
        "runs": [{"id": 3, "task_id": "t_test", "status": "done", "ended_at": 123}],
    }
    monkeypatch.setattr(Board, "card_show", lambda self, id: snapshot)
    return Reports(context), commit, snapshot


def test_report_pages_are_pinned_to_commit_and_do_not_read_uncommitted_changes(report, context):
    reader, commit, _ = report
    (context.workspace / "report.md").write_text("uncommitted change")
    parts, offset = [], 0
    while offset is not None:
        page = reader.card_report("t_test", 3, commit, "report.md", offset, 6000)
        parts.append(page["content"])
        assert page["commit"] == commit
        offset = page["next_offset"]
    assert ''.join(parts) == "Evidence 🎯\n" * 1000
    assert (context.workspace / "report.md").read_text() == "uncommitted change"


@pytest.mark.parametrize("path", ["../report.md", "/etc/passwd", "link.md", "missing.md", ":(glob)*", "./report.md"])
def test_report_rejects_unsafe_or_nonregular_paths(report, path):
    reader, commit, _ = report
    with pytest.raises(ValueError):
        reader.card_report("t_test", 3, commit, path)


def test_report_rejects_other_runs_commits_and_foreign_workspaces(report):
    reader, commit, snapshot = report
    with pytest.raises(ValueError, match="latest completed"):
        reader.card_report("t_test", 2, commit, "report.md")
    with pytest.raises(ValueError, match="not available"):
        reader.card_report("t_test", 3, "0" * 40, "report.md")
    snapshot["card"]["workspace_path"] = "/etc"
    with pytest.raises(ValueError, match="not on this profile host"):
        reader.card_report("t_test", 3, commit, "report.md")


def test_report_refuses_existing_unrelated_commit_and_oversized_content(report, context):
    reader, _commit, _ = report
    def git(*args):
        return subprocess.check_output(["git", "-C", str(context.workspace), *args], text=True).strip()
    git("checkout", "--orphan", "other-card")
    git("commit", "-m", "unrelated")
    other_commit = git("rev-parse", "HEAD")
    with pytest.raises(ValueError, match="not available"):
        reader.card_report("t_test", 3, other_commit, "report.md")
    git("checkout", "card/test")
    (context.workspace / "large.md").write_text("x" * 256001)
    git("add", "large.md")
    git("commit", "-m", "large report")
    with pytest.raises(ValueError, match="read limit"):
        reader.card_report("t_test", 3, git("rev-parse", "HEAD"), "large.md")

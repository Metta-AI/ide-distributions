"""The ``version`` tool: create (build, tag, trees, failures, background), adopt, read, list.

``Lab`` is the shared setup for the version, upload and submit tool tests: a workspace with
``cow_fake`` active and its ``code/`` repository laid out as the setup wake would leave it
(``policy.json`` at the root, the fakegame mixin under ``mixin/``, committed on ``main``).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

import research_core.tools.submit  # noqa: F401  (registers the tool)
import research_core.tools.upload  # noqa: F401  (registers the tool)
import research_core.tools.version  # noqa: F401  (registers the tool)
from research_core import gitops, mixin
from research_core.platform import submission
from research_core.platform.fake import FakePlatform
from research_core.store import capabilities, sessions, tasks
from research_core.store.actors import Actor
from research_core.tools import version as version_tool
from research_core.tools.base import run_tool
from tests.fixtures.fakegame_repo import COWORLD_ID, STARTER_POLICY, fixture_files

HARNESS = Actor.harness()


def setup_code_repo(ctx) -> Path:
    """``code/``: the starter ``policy.json`` and the fakegame mixin under ``mixin/``."""
    coworld = ctx.coworld()
    code = gitops.init_repo(coworld.code)
    for relative, content in fixture_files(COWORLD_ID).items():
        path = code / "mixin" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    parameters = STARTER_POLICY["parameters"]
    (code / "policy.json").write_bytes(policy_bytes(**parameters))
    gitops.commit_all(code, "Import the fakegame mixin")
    return code


def policy_bytes(aggression: int, patience: int) -> bytes:
    policy = {"parameters": {"aggression": aggression, "patience": patience}}
    return (json.dumps(policy) + "\n").encode()


def commit_policy(tree: Path, aggression: int, patience: int, message: str = "Tune") -> str:
    (tree / "policy.json").write_bytes(policy_bytes(aggression, patience))
    return gitops.commit_all(tree, message)


def set_build_command(code: Path, command: str) -> str:
    """Replace the mixin's build command and commit, so the tree stays clean."""
    path = code / "mixin" / "MIXIN.md"
    text = path.read_text()
    assert "  build: sh mixin/tools/build.sh\n" in text
    path.write_text(text.replace("  build: sh mixin/tools/build.sh\n", f"  build: {command}\n"))
    return gitops.commit_all(code, "Change the build command")


class Lab:
    """A workspace with the fakegame code repository and tool-call helpers."""

    def __init__(self, ctx_factory):
        self.ctx_factory = ctx_factory
        self.ctx = ctx_factory()
        self.coworld = self.ctx.coworld()
        self.code = setup_code_repo(self.ctx)
        self.store = self.ctx.store
        self.wstore = self.ctx.wstore

    def run(self, tool, args, *, role="orchestrator", session_id="s1", faults="", env=None):
        ctx = self.ctx_factory(role, session_id)
        if faults:
            ctx.env["IDE_FAKE_FAULTS"] = faults
        ctx.env.update(env or {})
        submission.invalidate()  # each real tool call is its own process
        return json.loads(run_tool(tool, args, ctx))

    def platform(self, faults: str = "") -> FakePlatform:
        return FakePlatform(
            self.ctx.workspace.state / "fake_platform.json", {"IDE_FAKE_FAULTS": faults}
        )

    def create(self, summary="A Version", **args) -> dict:
        result = self.run("version", {"action": "create", "summary": summary, **args})
        assert result["ok"] is True, result
        return result["record"]

    def upload(self, version: str, **kwargs) -> dict:
        result = self.run("upload", {"version": version}, **kwargs)
        assert result["ok"] is True, result
        return result

    def submit(self, version: str, **args) -> dict:
        kwargs = {k: args.pop(k) for k in ("faults", "session_id") if k in args}
        return self.run("submit", {"version": version, "league": "league_fake", **args}, **kwargs)

    def read(self, version: str) -> dict:
        result = self.run("version", {"action": "read", "version": version})
        assert result["ok"] is True, result
        return result

    def events(self, kind: str) -> list[dict]:
        rows = self.store.q("SELECT payload FROM events WHERE kind = ? ORDER BY seq", (kind,))
        return [json.loads(row["payload"]) for row in rows]


@pytest.fixture
def lab(ctx_factory) -> Lab:
    return Lab(ctx_factory)


# --- create ------------------------------------------------------------------------------


def test_version_create_builds_and_tags(lab):
    result = lab.run("version", {"action": "create", "summary": "The starter policy"})
    assert result["ok"] is True, result
    record = result["record"]
    head = gitops.rev_parse(lab.code, "HEAD")
    expected = policy_bytes(3, 5)
    assert record["id"] == "V-1" and record["source"] == "built"
    assert record["commit_sha"] == head and record["branch"] == "main"
    assert record["artifact"] == {
        "kind": "file",
        "path": "build/policy.json",
        "digest": hashlib.sha256(expected).hexdigest(),
        "size": len(expected),
    }
    assert record["policy_name"] == "cow_fake-main"
    assert record["submission_status"] == "built" and record["image"] is None
    assert gitops.rev_parse(lab.code, "v/1") == head
    kept = lab.coworld.documents / "artifacts" / record["artifact"]["digest"]
    assert kept.read_bytes() == expected
    assert result["build"]["log"].startswith(str(lab.coworld.documents / "logs" / "build-"))
    assert [e["commit_sha"] for e in lab.events("version.created")] == [head]
    # The build output does not make the tree dirty for the next Version.
    second = lab.create("Again", based_on=["V-1"])
    assert second["id"] == "V-2" and second["based_on"] == ["V-1"]
    listed = lab.run("version", {"action": "list", "limit": 5}, role="researcher")
    assert [r["id"] for r in listed["records"]] == ["V-2", "V-1"]


def test_version_create_records_the_runtime_image(lab, tmp_path):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / ".softmax-runtime.json").write_text(
        json.dumps({"image": "ghcr.io/softmax/autoresearch@sha256:abc", "profile": {}})
    )
    result = lab.run(
        "version", {"action": "create", "summary": "Hosted"}, env={"HERMES_HOME": str(home)}
    )
    assert result["record"]["image"] == "ghcr.io/softmax/autoresearch@sha256:abc"


def test_version_create_in_the_researchers_worktree(lab):
    worktree = gitops.worktree_add(lab.code, lab.coworld.worktrees / "task-1", branch="task/1")
    sha = commit_policy(worktree, 3, 7, "Raise patience")
    task = tasks.create(
        lab.store, HARNESS, type="maintenance", title="Tune", description="Tune patience."
    )
    researcher = Actor.session("s2")
    sessions.register(
        lab.wstore, HARNESS, session_id="s2", profile="researcher", kind="task", task_id=1
    )
    tasks.claim(
        lab.store, researcher, task["id"], run_id=None, worktree=str(worktree), branch="task/1"
    )

    record = lab.run(
        "version",
        {"action": "create", "summary": "Patience 7", "task": "T-1"},
        role="researcher",
        session_id="s2",
    )["record"]
    assert record["commit_sha"] == sha and record["branch"] == "task/1"
    assert (
        record["task_id"] == 1
        and record["artifact"]["digest"] == hashlib.sha256(policy_bytes(3, 7)).hexdigest()
    )
    assert gitops.rev_parse(lab.code, "v/1") == sha
    # The Session's Task is found without the task argument too.
    assert lab.run(
        "version", {"action": "create", "summary": "Same"}, role="researcher", session_id="s2"
    )["ok"]

    stranger = lab.run(
        "version", {"action": "create", "summary": "No"}, role="researcher", session_id="s3"
    )
    assert stranger["ok"] is False and "holds no Task" in stranger["error"]
    stale = lab.run(
        "version",
        {"action": "create", "summary": "No", "task": "T-1"},
        role="researcher",
        session_id="s3",
    )
    assert stale["ok"] is False and "does not hold it" in stale["error"]


def test_version_create_orchestrator_worktree(lab):
    orch = gitops.worktree_add(lab.code, lab.coworld.worktrees / "orch-1", branch="orch/1")
    sha = commit_policy(orch, 4, 5, "More aggression")
    record = lab.create("Aggression 4", worktree="orch-1")
    assert record["commit_sha"] == sha and record["branch"] == "orch/1"
    missing = lab.run("version", {"action": "create", "summary": "x", "worktree": "orch-9"})
    assert missing["ok"] is False and "orch-9" in missing["error"] and "orch-1" in missing["error"]


def test_version_create_refuses_dirty_tree(lab):
    (lab.code / "policy.json").write_bytes(policy_bytes(9, 9))
    (lab.code / "notes.md").write_text("scratch\n")
    result = lab.run("version", {"action": "create", "summary": "Dirty"})
    assert result["ok"] is False
    assert "uncommitted changes" in result["error"]
    assert sorted(result["context"]["dirty_files"]) == ["notes.md", "policy.json"]
    assert lab.store.one("SELECT COUNT(*) FROM versions")[0] == 0


def test_version_create_refuses_image(lab):
    path = lab.code / "mixin" / "MIXIN.md"
    path.write_text(path.read_text().replace("  kind: file\n", "  kind: image\n"))
    gitops.commit_all(lab.code, "Switch to an image policy")
    result = lab.run("version", {"action": "create", "summary": "Image"})
    assert result["ok"] is False
    assert result["error"] == version_tool.IMAGE_REFUSAL
    assert lab.store.one("SELECT COUNT(*) FROM versions")[0] == 0


def test_version_create_build_failure_reports_capability(lab):
    set_build_command(lab.code, "echo 'compiler exploded' >&2; exit 3")
    result = lab.run("version", {"action": "create", "summary": "Broken"})
    assert result["ok"] is False
    assert "exit code 3" in result["error"]
    context = result["context"]
    assert context["log"].startswith(str(lab.coworld.documents / "logs" / "build-"))
    assert "compiler exploded" in context["output_tail"]
    assert context["capability"] == "build" and context["maintenance_task"] == "T-1"
    broken = capabilities.read(lab.store, "build")
    assert broken["status"] == "broken" and broken["maintenance_task_id"] == 1
    assert "compiler exploded" in broken["output"]
    # A second failure joins the open repair Task instead of opening another.
    again = lab.run("version", {"action": "create", "summary": "Broken again"})
    assert again["ok"] is False and again["context"]["maintenance_task"] == "T-1"
    repairs = lab.store.q(
        "SELECT id FROM tasks WHERE type = 'maintenance' AND capability = 'build'"
    )
    assert len(repairs) == 1
    assert lab.store.one("SELECT COUNT(*) FROM versions")[0] == 0


def test_version_create_background_and_resume(lab, monkeypatch):
    set_build_command(lab.code, "sleep 0.6 && sh mixin/tools/build.sh")
    monkeypatch.setattr(version_tool, "BUILD_BUDGET_S", 0.1)
    started = lab.run("version", {"action": "create", "summary": "Slow"})
    assert started["ok"] is True and "record" not in started
    background = started["background"]
    assert background["pid"] > 0 and background["resume"] and "task.wait" in started["next"]
    deadline = time.time() + 10
    while not mixin.resume(background["resume"]).finished:
        assert time.time() < deadline, "the detached build never ended"
        time.sleep(0.05)
    finished = lab.run(
        "version", {"action": "create", "summary": "Slow", "resume": background["resume"]}
    )
    assert finished["ok"] is True, finished
    assert finished["record"]["id"] == "V-1"
    assert (
        finished["record"]["artifact"]["digest"] == hashlib.sha256(policy_bytes(3, 5)).hexdigest()
    )


# --- adopt, read -------------------------------------------------------------------------


def test_adopt(lab):
    platform = lab.platform()
    first = platform.upload_file_policy(name="starter", content=policy_bytes(3, 5))
    second = platform.upload_file_policy(name="starter", content=policy_bytes(3, 9))

    adopted = lab.run("version", {"action": "adopt", "label": "starter:v1", "summary": "Desktop"})
    assert adopted["ok"] is True, adopted
    record = adopted["record"]
    assert record["id"] == "V-1" and record["source"] == "adopted"
    assert record["uploaded"]["policy_version_id"] == first.id
    assert record["uploaded"]["digest"] == first.content_hash
    assert record["policy_name"] == "starter" and record["commit_sha"] is None
    assert record["submission_status"] == "uploaded"
    assert adopted["policy_version"]["label"] == "starter:v1"

    twice = lab.run("version", {"action": "adopt", "label": "starter:v1", "summary": "Again"})
    assert twice["ok"] is False and "already V-1" in twice["error"]
    by_id = lab.run("version", {"action": "adopt", "policy_version": second.id, "summary": "CLI"})
    assert by_id["ok"] is True and by_id["record"]["id"] == "V-2"
    unknown = lab.run("version", {"action": "adopt", "label": "starter:v7", "summary": "?"})
    assert unknown["ok"] is False and "starter:v1" in unknown["context"]["known"]
    neither = lab.run("version", {"action": "adopt", "summary": "?"})
    assert neither["ok"] is False and "policy_version" in neither["error"]

    read = lab.read("V-1")
    assert read["record"]["submission_status"] == "uploaded"
    assert read["platform"] == {"submissions": [], "memberships": []}

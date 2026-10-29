"""Coworld switching, source fetch, mixin import and lesson import (plan Task 4.7)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from research_core import cli, coworld_setup, gitops
from research_core.store import claims, coworlds, notices
from research_core.store.actors import Actor
from research_core.tools.base import ToolContext
from tests.fixtures.fakegame_repo import SKILL_NAMES, make_fakegame_repo

COWORLD = "cow_fake"


@pytest.fixture
def env(tmp_path):
    return {
        "IDE_WORKSPACE": str(tmp_path / "ws"),
        "IDE_PLATFORM": "fake",
        "IDE_SESSION_ID": "s1",
        "SOFTMAX_PLAYER_ID": "ply_test",
    }


@pytest.fixture
def ctx(env):
    context = ToolContext.from_env("orchestrator", env)
    context.workspace.ensure_dirs()
    return context


@pytest.fixture
def upstream(tmp_path, ctx):
    """The fakegame fixture repository, registered as the fake Coworld's pinned source."""
    repo, sha = make_fakegame_repo(tmp_path / "upstream")
    ctx.platform().configure(source_url=repo.as_uri(), source_commit=sha)
    return repo, sha


def activate(ctx, coworld_id=COWORLD):
    harness = Actor.harness()
    folder = ctx.workspace.coworld(coworld_id).folder_name
    coworlds.register(ctx.wstore, harness, coworld_id, folder=folder)
    coworlds.activate(ctx.wstore, harness, coworld_id)


def setup_events(ctx, coworld_id=COWORLD):
    return [
        e
        for e in ctx.wstore.events_after(0)
        if e.kind == "coworld.setup_needed" and e.subject == coworld_id
    ]


# --- fetch ------------------------------------------------------------------------------------


def test_fetch_imports_the_mixin_lessons_and_commits_on_main(ctx, upstream):
    repo, sha = upstream
    activate(ctx)
    result = coworld_setup.fetch(ctx, COWORLD)
    coworld = ctx.workspace.coworld(COWORLD)

    assert result["problems"] == [], result["problems"]
    assert result["source"] == {"url": repo.as_uri(), "commit": sha, "path": str(coworld.source)}
    assert gitops.rev_parse(coworld.source, "HEAD") == sha
    assert (
        result["coworld_md"]["path"] == "COWORLD.md" and "fakegame" in result["coworld_md"]["text"]
    )

    mixin = result["mixin"]
    assert mixin["source_dir"] == "." and mixin["coworld"] == COWORLD
    assert mixin["commands"] == ["build", "check", "evaluate", "upload"]
    assert (coworld.mixin / "MIXIN.md").is_file() and (
        coworld.mixin / "tools" / "build.sh"
    ).is_file()
    assert not (coworld.mixin / ".git").exists()
    assert [s["to"] for s in mixin["skills"]] == sorted(f"fakegame-{n}" for n in SKILL_NAMES)
    for name in SKILL_NAMES:
        assert not (coworld.mixin / "skills" / name).exists()
        text = (coworld.mixin / "skills" / f"fakegame-{name}" / "SKILL.md").read_text()
        assert text.startswith("---\n") and f"\nname: fakegame-{name}\n" in text
        assert f"\nname: {name}\n" not in text
    assert result["toolchains"] == []

    lessons = result["lessons"]
    assert lessons["claims"] == 2 and lessons["lessons"] == 2 and lessons["skipped"] == []
    assert lessons["created"] == ["C-1", "C-2", "C-3", "C-4"]

    assert result["commit"] == gitops.rev_parse(coworld.code, "main")
    subjects = [entry["subject"] for entry in gitops.log(coworld.code, "main", 5)]
    assert subjects == [f"Import mixin from {repo.as_uri()}@{sha}", "Initialize repository"]
    assert gitops.is_clean(coworld.code)


def test_imported_claims_carry_doc_evidence_and_tags(ctx, upstream):
    activate(ctx)
    coworld_setup.fetch(ctx, COWORLD)
    store = ctx.store

    claim = claims.read(store, "C-1")
    assert claim["summary"].startswith("Score rises by 0.5 per aggression point above 3")
    assert claim["tags"] == ["mechanism", "mechanics", "imported"]
    assert claim["body"].startswith("What would change our mind: The cited mechanics document")
    assert claim["status"] == "active" and claim["counts"]["supports"] == 2
    refs = {(e["type"], e["ref"]) for e in claim["evidence"]}
    seed_file = "seed/kb/claims/C-01HFAKE0000000000000000C01--score-follows-aggression.md"
    assert refs == {("doc", f"{seed_file}#L19"), ("doc", "docs/mechanics.md#L8-L9")}
    cited = next(e for e in claim["evidence"] if e["ref"].startswith("docs/"))
    assert cited["note"].startswith("imported from the Coworld repository at 000000000000: ")
    assert "About 50, plus a board term set by the seed" in cited["note"]
    seed_link = next(e for e in claim["evidence"] if e["ref"].startswith("seed/"))
    assert "last checked Coworld version 1" in seed_link["note"]

    lesson = claims.read(store, "C-3")
    assert lesson["tags"] == ["lesson", "imported"]
    assert lesson["summary"].startswith("Compare candidate and baseline on the same seeds")
    assert lesson["body"].startswith("Provenance: the fakegame mechanics document.")
    assert {e["ref"] for e in lesson["evidence"]} == {
        "seed/lessons/L-01HFAKE0000000000000000X01.md#L12",
        "docs/mechanics.md#L8-L9",
    }


def test_fetch_refuses_a_mixin_that_declares_another_coworld(ctx, tmp_path):
    other, sha = make_fakegame_repo(tmp_path / "other", coworld_id="cow_other")
    activate(ctx)
    result = coworld_setup.fetch(ctx, COWORLD, source=other.as_uri(), commit=sha)
    coworld = ctx.workspace.coworld(COWORLD)

    assert result["mixin"] is None
    assert result["problem"] == "MIXIN.md declares cow_other, workspace is cow_fake"
    assert result["problem"] in result["problems"]
    assert not coworld.mixin.exists()
    assert result["lessons"]["claims"] == 0 and result["commit"] is None
    assert ctx.store.one("SELECT COUNT(*) FROM claims")[0] == 0
    assert coworlds.read(ctx.wstore, COWORLD)["set_up_at"] is None


def test_fetch_without_a_source_url_reports_the_gap(ctx):
    activate(ctx)
    result = coworld_setup.fetch(ctx, COWORLD)
    assert result["mixin"] is None and result["source"]["url"] is None
    assert any("no source repository" in p for p in result["problems"])


def test_fetch_reuses_a_clone_already_at_the_commit(ctx, upstream, monkeypatch):
    repo, sha = upstream
    activate(ctx)
    coworld_setup.fetch(ctx, COWORLD)

    def no_clone(*args, **kwargs):
        raise AssertionError("clone_pinned must not run for a clone already at the commit")

    monkeypatch.setattr(gitops, "clone_pinned", no_clone)
    again = coworld_setup.fetch(ctx, COWORLD)
    assert again["source"]["commit"] == sha
    assert again["lessons"]["claims"] == 0 and len(again["lessons"]["skipped"]) == 4
    assert all("already imported" in s["reason"] for s in again["lessons"]["skipped"])
    assert again["commit"] is None  # nothing changed in code/


def test_find_mixin_prefers_the_shallowest(tmp_path):
    source = tmp_path / "src"
    (source / "games" / "deep" / "deeper").mkdir(parents=True)
    (source / "games" / "deep" / "deeper" / "MIXIN.md").write_text("---\n---\n")
    (source / ".git").mkdir()
    (source / ".git" / "MIXIN.md").write_text("ignored")
    assert coworld_setup.find_mixin(source) == source / "games" / "deep" / "deeper"
    (source / "games" / "MIXIN.md").write_text("---\n---\n")
    assert coworld_setup.find_mixin(source) == source / "games"
    assert coworld_setup.find_mixin(tmp_path / "empty") is None


# --- lesson import edge cases ---------------------------------------------------------------


def test_import_lessons_collects_bad_files_and_keeps_going(ctx, upstream):
    repo, _ = upstream
    activate(ctx)
    seed = repo / "seed"
    (seed / "kb" / "claims" / "C-bad--no-front-matter.md").write_text("Just a sentence.\n")
    (seed / "kb" / "claims" / "C-bad2--bad-yaml.md").write_text("---\nkind: [\n---\n\nText.\n")
    (seed / "kb" / "claims" / "C-empty--empty.md").write_text("---\nkind: x\n---\n\n")
    (seed / "evidence" / "broken.json").write_text("{")
    (seed / "evidence" / "source-citations.json").write_text("{")

    counts = coworld_setup.import_lessons(ctx, repo)
    assert counts["claims"] == 2 and counts["lessons"] == 2
    reasons = {s["file"]: s["reason"] for s in counts["skipped"]}
    assert reasons["seed/kb/claims/C-bad--no-front-matter.md"].startswith("no front matter")
    assert "not valid YAML" in reasons["seed/kb/claims/C-bad2--bad-yaml.md"]
    assert reasons["seed/kb/claims/C-empty--empty.md"] == "empty body"
    assert reasons["seed/evidence/source-citations.json"].startswith("not JSON")
    # Without readable citations the claims still cite their seed file.
    assert claims.read(ctx.store, "C-1")["counts"]["supports"] == 1


def test_import_caps_long_summaries_at_forty_words(ctx, upstream):
    repo, _ = upstream
    activate(ctx)
    words = " ".join(f"w{i}" for i in range(60))
    (repo / "seed" / "kb" / "claims" / "C-long--long.md").write_text(
        f"---\nkind: method\ntopics: [a, b]\n---\n\n{words}. Second sentence here.\n"
    )
    counts = coworld_setup.import_lessons(ctx, repo)
    created = next(
        claims.read(ctx.store, c)
        for c in counts["created"]
        if "method" in claims.read(ctx.store, c)["tags"]
    )
    assert len(created["summary"].split()) == 40 and created["summary"].endswith("…")
    assert created["body"] == "Second sentence here."
    assert created["tags"] == ["method", "a", "b", "imported"]


# --- switch -----------------------------------------------------------------------------------


def test_switch_twice_flips_active_and_emits_setup_needed_once(ctx, upstream, tmp_path):
    repo, sha = upstream
    request = {"text": "submit the starter as my champion", "cites": "msg-1"}
    drained = []

    first = coworld_setup.switch(
        ctx, COWORLD, cites="msg-1", pending_request=request, drain=lambda: drained.append(1)
    )
    assert drained == [1] and first["switched_from"] is None
    assert first["code"]["initialized"] is True
    assert first["setup"]["status"] == "needed" and first["setup"]["fetch"]["mixin"] is not None
    assert first["profile_config"]["note"].startswith("HERMES_HOME is not set")
    assert coworlds.active(ctx.wstore)["coworld_id"] == COWORLD
    assert coworlds.read(ctx.wstore, COWORLD)["pending_request"] == request
    events = setup_events(ctx)
    assert len(events) == 1 and events[0].seq == first["setup"]["event_seq"]
    assert events[0].payload == {
        "coworld_id": COWORLD,
        "mixin": "found",
        "source": {"url": repo.as_uri(), "commit": sha},
        "lessons": first["setup"]["fetch"]["lessons"],
        "pending_request": request,
        "cites": "msg-1",
        "problems": [],
    }
    notice = notices.unread(ctx.wstore)
    assert len(notice) == 1 and notice[0]["text"].startswith("Switching to cow_fake;")
    assert notice[0]["refs"] == ["msg-1"] and first["notice"] == notice[0]["id"]

    # A second switch to the same Coworld while its setup is pending changes nothing more.
    second = coworld_setup.switch(ctx, COWORLD, pending_request={"text": "new", "cites": "msg-2"})
    assert second["setup"]["status"] == "pending"
    assert second["setup"]["event_seq"] == first["setup"]["event_seq"]
    assert second["code"]["initialized"] is False and "notice" not in second
    assert len(setup_events(ctx)) == 1
    assert coworlds.read(ctx.wstore, COWORLD)["pending_request"]["text"] == "new"

    # Switching to another Coworld flips the active row; its own setup is announced once.
    other, other_sha = make_fakegame_repo(tmp_path / "other", coworld_id="cow_other")
    third = coworld_setup.switch(ctx, "cow_other", source=other.as_uri(), commit=other_sha)
    assert third["switched_from"] == COWORLD
    assert coworlds.active(ctx.wstore)["coworld_id"] == "cow_other"
    assert third["setup"]["status"] == "needed"
    assert third["setup"]["fetch"]["mixin"]["skills"][0]["to"] == "cow_other-ab"
    assert any("no manifest for cow_other" in p for p in third["setup"]["fetch"]["problems"])
    assert len(setup_events(ctx, "cow_other")) == 1 and len(setup_events(ctx)) == 1
    assert ctx.store.path == ctx.workspace.coworld("cow_other").store_path
    assert ctx.store.one("SELECT COUNT(*) FROM claims")[0] == 4

    back = coworld_setup.switch(ctx, COWORLD)
    assert back["switched_from"] == "cow_other" and back["setup"]["status"] == "pending"
    assert coworlds.active(ctx.wstore)["coworld_id"] == COWORLD
    switched = [e.payload for e in ctx.wstore.events_after(0) if e.kind == "coworld.switched"]
    assert switched == [
        {"from": None, "to": COWORLD},
        {"from": COWORLD, "to": "cow_other"},
        {"from": "cow_other", "to": COWORLD},
    ]


def test_switch_refuses_a_bad_id_and_a_bad_pending_request(ctx):
    from research_core.tools.base import ToolError

    with pytest.raises(ToolError, match="not a Coworld id"):
        coworld_setup.switch(ctx, "bad id")
    with pytest.raises(ToolError, match="pending_request must be an object"):
        coworld_setup.switch(ctx, COWORLD, pending_request="text")
    assert coworlds.active(ctx.wstore) is None


def test_switch_records_skill_override_upstream_shas(ctx, upstream, monkeypatch, tmp_path):
    core_skills = tmp_path / "core_skills"
    (core_skills / "common" / "research" / "survey").mkdir(parents=True)
    (core_skills / "common" / "research" / "survey" / "SKILL.md").write_text("core survey v1\n")
    monkeypatch.setattr(coworld_setup, "core_skill_sha", lambda name: _sha(core_skills, name))
    coworld = ctx.workspace.coworld(COWORLD)
    override = coworld.code / ".agents" / "skills" / "survey"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("my survey\n")
    (coworld.code / ".agents" / "skills" / "unknown").mkdir()

    result = coworld_setup.switch(ctx, COWORLD)
    recorded = (override / ".upstream-sha").read_text().strip()
    assert recorded == _sha(core_skills, "survey")
    assert result["skill_overrides"] == [
        {"name": "survey", "upstream_sha": recorded, "core_sha": recorded, "stale": False},
        {"name": "unknown", "upstream_sha": None, "core_sha": None, "stale": False},
    ]
    (core_skills / "common" / "research" / "survey" / "SKILL.md").write_text("core survey v2\n")
    stale = coworld_setup.skill_overrides(coworld.code)
    assert stale[0]["stale"] is True and stale[0]["upstream_sha"] == recorded


def _sha(root: Path, name: str) -> str | None:
    import hashlib

    matches = sorted(root.glob(f"**/{name}/SKILL.md"))
    return hashlib.sha256(matches[0].read_bytes()).hexdigest() if matches else None


# --- profile config (A5.9) --------------------------------------------------------------------


def test_apply_profile_config_sets_the_keys_in_every_profile(tmp_path, env):
    profiles = tmp_path / "profiles"
    for name in ("orchestrator", "researcher-1", "researcher-2"):
        (profiles / name).mkdir(parents=True)
    calls: list[tuple[str, list[str]]] = []

    def run(argv, env=None, capture_output=True, text=True, check=False):
        calls.append((Path(env["HERMES_HOME"]).name, list(argv)))
        if argv[1:3] == ["config", "get"]:
            return subprocess.CompletedProcess(argv, 0, "null", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    ctx = ToolContext.from_env(
        "orchestrator",
        {
            **env,
            "HERMES_HOME": str(profiles / "orchestrator"),
            "HERMES_BIN": "/opt/fake/hermes",
            "IDE_RESEARCHER_PROFILES": "researcher-1,researcher-2,missing",
        },
    )
    coworld = ctx.workspace.coworld(COWORLD)
    skills = coworld.mixin / "skills"
    result = coworld_setup.apply_profile_config(ctx, coworld, mixin_skills_dir=skills, run=run)

    assert result["problems"] == [
        "profile missing: no profile home at " + str(profiles / "missing")
    ]
    assert all(argv[0] == "/opt/fake/hermes" for _, argv in calls)
    sets = [(home, argv) for home, argv in calls if argv[1:3] == ["config", "set"]]
    assert {home for home, _ in sets} == {"orchestrator", "researcher-1", "researcher-2"}
    assert {argv[3] for _, argv in sets} == {
        "skills.external_dirs",
        "skills.trusted_project_dirs",
        "terminal.cwd",
    }
    for home, role in (("orchestrator", "orchestrator"), ("researcher-2", "researcher")):
        dirs = json.loads(
            next(argv[4] for h, argv in sets if h == home and argv[3].endswith("dirs"))
        )
        assert dirs[-1] == str(skills) and dirs[0].endswith("skills/common")
        assert dirs[1].endswith(f"skills/{role}")
    cwd = next(argv[4] for _, argv in sets if argv[3] == "terminal.cwd")
    trusted = next(argv[4] for _, argv in sets if argv[3] == "skills.trusted_project_dirs")
    assert cwd == str(coworld.code) and json.loads(trusted) == [str(coworld.code)]
    assert len(result["applied"]) == 9


def test_apply_profile_config_without_a_hermes_executable(tmp_path, env, monkeypatch):
    monkeypatch.setattr(coworld_setup.shutil, "which", lambda name: None)
    ctx = ToolContext.from_env("orchestrator", {**env, "HERMES_HOME": str(tmp_path / "p")})
    coworld = ctx.workspace.coworld(COWORLD)
    result = coworld_setup.apply_profile_config(ctx, coworld, mixin_skills_dir=coworld.mixin)
    assert result["applied"] == [] and "hermes executable not found" in result["note"]


# --- toolchains -------------------------------------------------------------------------------


def test_install_toolchains_supports_python_and_reports_the_rest(ctx):
    ran: list[list[str]] = []

    def run(argv, capture_output=True, text=True, check=False):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    ws = ctx.workspace
    result = coworld_setup.install_toolchains(
        ws,
        [
            {"kind": "python", "version": "3.11"},
            {"kind": "nim", "version": "2.0.8"},
            {"kind": "python"},
        ],
        run=run,
    )
    target = ws.tools / "python-3.11"
    assert ran == [
        ["uv", "python", "install", "3.11"],
        ["uv", "venv", "--python", "3.11", str(target)],
    ]
    assert result["toolchains"] == [
        {"kind": "python", "version": "3.11", "path": str(target), "status": "installed"},
        {"kind": "nim", "version": "2.0.8", "status": "unsupported"},
        {"kind": "python", "version": None, "status": "failed"},
    ]
    assert result["problems"][0].startswith("toolchain nim 2.0.8: setup installs only python")
    assert result["problems"][1] == "toolchain python: a version is required"

    def failing(argv, capture_output=True, text=True, check=False):
        return subprocess.CompletedProcess(argv, 2, "out", "no such python")

    failed = coworld_setup.install_toolchains(
        ws, [{"kind": "python", "version": "3.9"}], run=failing
    )
    assert failed["toolchains"][0]["status"] == "failed"
    assert "exit code 2" in failed["problems"][0] and "no such python" in failed["problems"][0]


# --- setup status and the CLI -----------------------------------------------------------------


def test_setup_status_reports_the_mixin_and_pending_request(ctx, upstream):
    assert coworld_setup.setup_status(ctx)["coworld"] is None
    request = {"text": "go", "cites": "msg-1"}
    coworld_setup.switch(ctx, COWORLD, pending_request=request)
    status = coworld_setup.setup_status(ctx)
    assert status["coworld"]["coworld_id"] == COWORLD and status["code_repo"] is True
    assert status["source"]["present"] is True and len(status["source"]["commit"]) == 40
    assert status["mixin"]["present"] is True and status["mixin"]["coworld"] == COWORLD
    assert status["mixin"]["commands"] == ["build", "check", "evaluate", "upload"]
    assert status["mixin"]["skills"] == sorted(f"fakegame-{n}" for n in SKILL_NAMES)
    assert status["capabilities"] == [] and status["pending_request"] == request
    assert status["setup_pending_event"] is not None


def test_research_coworld_cli(ctx, upstream, env, monkeypatch, capsys):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert cli.main(["coworld", "switch", COWORLD, "--cites", "msg-1"]) == 0
    switched = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert switched["ok"] is True and switched["setup"]["status"] == "needed"
    assert switched["code"]["path"] == str(ctx.workspace.coworld(COWORLD).code)

    assert cli.main(["coworld", "setup-status"]) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["coworld"]["coworld_id"] == COWORLD and status["mixin"]["present"] is True
    assert ".state" not in json.dumps(status)

    assert cli.main(["coworld", "switch", "not ok"]) == 1
    assert (
        "not a Coworld id" in json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"]
    )

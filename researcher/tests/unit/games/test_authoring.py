"""Game folders: the scaffold (``game new``), a mixin import, the install gate (``game check``)
and installation (``game install``) on a temporary host."""

import json
import os
from pathlib import Path

import pytest

from ide_research_core.cli import main
from ide_research_core.games import authoring, scope
from ide_research_core.io import gitops
from ide_research_core.io.docs import read_doc

LEAGUE = "league_test"


@pytest.fixture
def host(tmp_path):
    home = tmp_path / "host" / "profiles" / "orchestrator"
    home.mkdir(parents=True)
    return {"HERMES_HOME": str(home), "PATH": os.environ["PATH"]}


@pytest.fixture
def card(tmp_path):
    """A card worktree stand-in: a git repository the folder is committed in."""
    repo = tmp_path / "card"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "T")
    return repo


def fill(folder: Path) -> None:
    """The one front matter choice the template leaves to the author."""
    path = folder / "GAME.md"
    path.write_text(path.read_text().replace("{{METRIC}}", "score"))


def commit(repo: Path) -> str:
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "game folder")
    return gitops.head(repo)


def test_new_scaffolds_a_loadable_folder_with_reported_gaps(tmp_path):
    made = authoring.new("newgame", LEAGUE, tmp_path, title="New Game")
    folder = Path(made["path"])
    assert folder == tmp_path / "games" / "newgame"
    assert not (folder / "CONTRACT.md").exists()
    meta, body = read_doc(folder / "GAME.md")
    assert meta["name"] == "newgame" and meta["league_id"] == LEAGUE and "source" not in meta
    assert body.startswith("# New Game") and "{{" not in body
    assert made["check"]["problems"] == ["GAME.md front matter still holds a {{...}} placeholder"]
    fill(folder)
    report = authoring.check("newgame", tmp_path)
    assert report["ok"] and report["problems"] == []
    assert "binding evaluate is still the template stub" in report["gaps"]
    assert "binding skill newgame-ab is still the template" in report["gaps"]
    assert "GAME.md section `Starter policy` is still the template" in report["gaps"]
    assert sorted(report["skills"]) == sorted(
        f"newgame-{b}" for b in authoring.KNOWLEDGE_BINDINGS
    )
    with pytest.raises(authoring.AuthoringError):
        authoring.new("newgame", LEAGUE, tmp_path)


def test_new_with_a_source_records_it(tmp_path):
    source = {"repo": "https://github.com/a/game", "commit": "c" * 40}
    made = authoring.new("newgame", LEAGUE, tmp_path, source=source)
    meta, _ = read_doc(Path(made["path"]) / "GAME.md")
    assert meta["source"] == {"repo": "https://github.com/a/game", "pin": "coworld_manifest"}
    assert "https://github.com/a/game at `cccc" in (Path(made["path"]) / "PROVENANCE.md").read_text()


def test_a_mixin_is_vendored_and_its_skills_renamed(tmp_path):
    snapshot = tmp_path / "mixin-snapshot"
    (snapshot / "skills" / "ab").mkdir(parents=True)
    (snapshot / "skills" / "ab" / "SKILL.md").write_text(
        "---\nname: crewrift-ab\nbinding: ab\ndescription: Crewrift A/B.\n---\n\n# Real A/B\n"
    )
    (snapshot / "skills" / "studio").mkdir()
    (snapshot / "skills" / "studio" / "SKILL.md").write_text(
        "---\nname: x\ndescription: Extra.\n---\n"
    )
    (snapshot / "docs").mkdir()
    (snapshot / "docs" / "game.md").write_text("rules\n")
    (snapshot / "MIXIN.md").write_text("# MIXIN\n")
    mixin = {"repo": "https://github.com/a/game-mixin", "commit": "m" * 40, "path": str(snapshot)}
    made = authoring.new("newgame", LEAGUE, tmp_path / "out", mixin=mixin)
    folder = Path(made["path"])
    assert made["mixin_skills"] == ["newgame-ab", "newgame-studio"]
    assert (folder / "mixin" / "MIXIN.md").is_file() and (folder / "mixin" / "docs" / "game.md").is_file()
    assert not (folder / "mixin" / "skills").exists()
    meta, body = read_doc(folder / "skills" / "ab" / "SKILL.md")
    assert meta["name"] == "newgame-ab" and meta["binding"] == "ab" and "# Real A/B" in body
    assert "a/game-mixin at `mmmm" in (folder / "GAME.md").read_text()
    fill(folder)
    report = authoring.check("newgame", tmp_path / "out")
    assert report["ok"], report["problems"]
    assert "binding skill newgame-ab is still the template" not in report["gaps"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda f: (f / "GAME.md").write_text(
                (f / "GAME.md").read_text().replace("games/newgame/tools/build.py", "games/gota/tools/build.py")
            ),
            "names a path outside games/newgame/",
        ),
        (
            lambda f: (f / "GAME.md").write_text(
                (f / "GAME.md").read_text().replace(
                    "games/newgame/tools/build.py", "games/newgame/../fakegame/tools/fake_build.py"
                )
            ),
            "names a path outside games/newgame/",
        ),
        (lambda f: (f / "tools" / "evaluate.py").unlink(), "evaluate.py does not exist"),
        (lambda f: (f / "seed" / "plan.md").unlink(), "seed/plan.md does not exist"),
        (lambda f: (f / "tools" / "link").symlink_to("/etc"), "tools/link is a symbolic link"),
        (
            lambda f: (f / "docs").mkdir() or (f / "docs" / "SKILL.md").write_text("---\nname: a\n---\n"),
            "a skill must be at skills/<skill>/SKILL.md",
        ),
        (
            lambda f: (f / "skills" / "ab" / "SKILL.md").write_text("---\nname: ab\ndescription: d\n---\n"),
            "name must be 'newgame-ab'",
        ),
        (
            lambda f: (f / "GAME.md").write_text(
                (f / "GAME.md").read_text().replace("## Seats and opponents", "## Seats")
            ),
            "no `## Seats and opponents` section",
        ),
    ],
)
def test_check_problems_block(tmp_path, change, problem):
    folder = Path(authoring.new("newgame", LEAGUE, tmp_path)["path"])
    fill(folder)
    change(folder)
    report = authoring.check("newgame", tmp_path)
    assert not report["ok"]
    assert any(problem in line for line in report["problems"]), report["problems"]


def test_check_reports_missing_host_requirements_as_gaps(tmp_path, host):
    folder = Path(authoring.new("newgame", LEAGUE, tmp_path)["path"])
    fill(folder)
    path = folder / "GAME.md"
    path.write_text(
        path.read_text().replace(
            "compiler: none\n",
            "compiler: none\nhost_requirements: {env: [NO_SUCH_VAR], tools: [no-such-tool]}\n",
        )
    )
    report = authoring.check("newgame", tmp_path, host)
    assert report["ok"]
    assert report["host_missing"] == {"env": ["NO_SUCH_VAR"], "tools": ["no-such-tool"]}


def test_install_copies_the_committed_folder_once(card, host):
    folder = Path(authoring.new("newgame", LEAGUE, card)["path"])
    fill(folder)
    (folder / "notes.txt").write_text("committed\n")
    first = commit(card)
    (card / "games" / "newgame" / "scratch.txt").write_text("not committed\n")
    installed = authoring.install("newgame", card, first, env=host, card="t_1", run_id=3)
    target = scope.installed_games_dir(host) / "newgame"
    assert installed["installed"] == str(target) and not installed["already_installed"]
    assert not (target / "scratch.txt").exists() and (target / "notes.txt").exists()
    record = json.loads((target / authoring.INSTALL_RECORD).read_text())
    assert record["commit"] == first and record["card"] == "t_1" and record["run_id"] == 3
    assert scope.locate_game("newgame", host) == scope.host_root(host)
    again = authoring.install("newgame", card, first, env=host)
    assert again["already_installed"]
    (folder / "docs").mkdir()
    (folder / "docs" / "more.md").write_text("more\n")
    second = commit(card)
    with pytest.raises(authoring.AuthoringError) as error:
        authoring.install("newgame", card, second, env=host)
    assert error.value.reason == "already_installed"
    replaced = authoring.install("newgame", card, second, env=host, replace=True)
    assert (target / "docs" / "more.md").exists()
    assert Path(replaced["replaced"]).parent == scope.host_root(host) / "game-history" / "newgame"


def test_install_refuses_shipped_names_and_failing_folders(card, host):
    with pytest.raises(authoring.AuthoringError) as error:
        authoring.install("fakegame", card, "HEAD", env=host)
    assert error.value.reason == "shipped"
    authoring.new("newgame", LEAGUE, card)  # unfilled: a front matter placeholder remains
    sha = commit(card)
    with pytest.raises(authoring.AuthoringError) as error:
        authoring.install("newgame", card, sha, env=host)
    assert error.value.reason == "invalid_game" and error.value.details["problems"]
    assert not (scope.installed_games_dir(host) / "newgame").exists()


def test_researchers_cannot_install(card, host, monkeypatch, capsys):
    (Path(host["HERMES_HOME"]) / "plugins" / "researcher_guard").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", host["HERMES_HOME"])
    code = main(["game", "install", "--name", "newgame", "--worktree", str(card), "--commit", "x"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["reason"] == "coordinator_required"


def test_a_mixin_with_a_symbolic_link_is_refused_before_copying(tmp_path):
    snapshot = tmp_path / "mixin-snapshot"
    (snapshot / "skills" / "ab").mkdir(parents=True)
    (tmp_path / "victim.md").write_text("name: keep\n")
    (snapshot / "skills" / "ab" / "SKILL.md").symlink_to(tmp_path / "victim.md")
    mixin = {"repo": "https://github.com/a/m", "commit": "m" * 40, "path": str(snapshot)}
    with pytest.raises(authoring.AuthoringError) as error:
        authoring.new("newgame", LEAGUE, tmp_path / "out", mixin=mixin)
    assert error.value.reason == "invalid_mixin"
    assert (tmp_path / "victim.md").read_text() == "name: keep\n"
    assert not (tmp_path / "out" / "games" / "newgame").exists()


def test_the_install_record_name_is_reserved(tmp_path):
    folder = Path(authoring.new("newgame", LEAGUE, tmp_path)["path"])
    fill(folder)
    (folder / authoring.INSTALL_RECORD).mkdir()
    assert any("reserved" in line for line in authoring.check("newgame", tmp_path)["problems"])


def test_a_late_retry_never_undoes_a_newer_installation(card, host):
    folder = Path(authoring.new("newgame", LEAGUE, card)["path"])
    fill(folder)
    first = commit(card)
    (folder / "docs").mkdir()
    (folder / "docs" / "b.md").write_text("b\n")
    second = commit(card)
    authoring.install("newgame", card, first, env=host, card="card-a", run_id=1)
    authoring.install("newgame", card, second, env=host, replace=True, card="card-b", run_id=1)
    retry = authoring.install("newgame", card, first, env=host, replace=True, card="card-a", run_id=1)
    target = scope.installed_games_dir(host) / "newgame"
    assert retry["already_installed"] and (target / "docs" / "b.md").exists()
    # Two replacements in the same second keep both previous folders.
    authoring.install("newgame", card, first, env=host, replace=True, card="card-c", run_id=1)
    authoring.install("newgame", card, second, env=host, replace=True, card="card-d", run_id=1)
    history = scope.host_root(host) / "game-history" / "newgame"
    assert len([p for p in history.iterdir() if p.name != "receipts"]) == 3


def test_a_same_commit_success_also_protects_its_retry(card, host):
    folder = Path(authoring.new("newgame", LEAGUE, card)["path"])
    fill(folder)
    first = commit(card)
    (folder / "docs").mkdir()
    (folder / "docs" / "b.md").write_text("b\n")
    second = commit(card)
    authoring.install("newgame", card, first, env=host, card="card-a", run_id=1)
    same = authoring.install("newgame", card, first, env=host, replace=True, card="card-x", run_id=1)
    assert same["already_installed"]
    authoring.install("newgame", card, second, env=host, replace=True, card="card-b", run_id=1)
    authoring.install("newgame", card, first, env=host, replace=True, card="card-x", run_id=1)
    assert (scope.installed_games_dir(host) / "newgame" / "docs" / "b.md").exists()


def test_an_unreadable_receipt_falls_back_to_the_install_record(card, host):
    folder = Path(authoring.new("newgame", LEAGUE, card)["path"])
    fill(folder)
    first = commit(card)
    authoring.install("newgame", card, first, env=host, card="card-a", run_id=1)
    receipt = scope.host_root(host) / "game-history" / "newgame" / "receipts" / "card-a-1.json"
    receipt.write_text("{truncated")
    retry = authoring.install("newgame", card, first, env=host, card="card-a", run_id=1)
    assert retry["already_installed"] and json.loads(receipt.read_text())["already_installed"]


def test_an_oversized_archive_is_refused(card, host, monkeypatch):
    from ide_research_core.io import archive

    folder = Path(authoring.new("newgame", LEAGUE, card)["path"])
    fill(folder)
    sha = commit(card)
    monkeypatch.setattr(archive, "MAX_FILE_BYTES", 10)
    with pytest.raises(authoring.AuthoringError) as error:
        authoring.install("newgame", card, sha, env=host)
    assert error.value.reason == "invalid_archive"
    assert not (scope.installed_games_dir(host) / "newgame").exists()

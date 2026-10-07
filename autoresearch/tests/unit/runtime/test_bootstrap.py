"""Bootstrap (Overview C1 with A2, A6, A7; C9 config migrations)."""

import os
import stat
from pathlib import Path

import yaml

from autoresearch.io import gitops
from autoresearch.io.docs import read_json
from autoresearch.queue.file_queue import FileQueue
from autoresearch.runctx import RunContext
from autoresearch.runtime import config_migrations
from autoresearch.runtime.bootstrap import bootstrap
from runtime.workspace import AUTORESEARCH_DIR

GAME = "fakegame"


class Recorder:
    """A fake ``hermes`` runner that records commands and applies ``config set`` to the
    profile config.yaml, as Hermes would."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], home: Path) -> None:
        self.calls.append(argv)
        path = home / "config.yaml"
        config = yaml.safe_load(path.read_text()) if path.exists() else {}
        config = config or {}
        node = config
        *parents, last = argv[2].split(".")
        for part in parents:
            node = node.setdefault(part, {})
        if argv[1] == "set":
            # Hermes keeps "off"/"on" as strings (hermes-facts item 8); YAML 1.1 would not.
            value = argv[3]
            node[last] = value if value in ("off", "on") else yaml.safe_load(value)
        else:
            node.pop(last, None)
        path.write_text(yaml.safe_dump(config))


def setup(tmp_path: Path, remote: bool = True):
    home = tmp_path / "profiles" / "autoresearch"
    home.mkdir(parents=True)
    origin = tmp_path / "origin.git"
    gitops.git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    env = {"HERMES_HOME": str(home), "AUTORESEARCH_GAME": GAME}
    if remote:
        env["AUTORESEARCH_WORKSPACE_REMOTE"] = str(origin)
    ctx = RunContext(home, home / "workspace", None, None, None)
    return ctx, env, origin, FileQueue(home / "board")


def run(ctx, env, queue, runner=None, **kwargs):
    return bootstrap(ctx, env, queue, runner=runner or Recorder(), **kwargs)


def test_fresh_bootstrap_seeds_pushes_and_creates_the_board(tmp_path):
    ctx, env, origin, queue = setup(tmp_path)

    result = run(ctx, env, queue)

    assert result.ok, result.error
    canonical = ctx.canonical
    assert (canonical / "plan.md").read_text() == (
        AUTORESEARCH_DIR / "games" / GAME / "seed" / "plan.md"
    ).read_text()
    assert (canonical / "strategy" / "STRATEGY.md").exists()
    assert "Plan 03 Task 4.1" in (canonical / "AGENTS.md").read_text()
    gitignore = (canonical / ".gitignore").read_text().splitlines()
    assert ".supervisor/runtime/" in gitignore and ".worktrees/" in gitignore
    assert gitops.git(canonical, "remote", "get-url", "origin") == str(origin)
    assert gitops.git(origin, "rev-parse", "main") == gitops.head(canonical)
    assert gitops.git(canonical, "status", "--porcelain") == ""
    assert queue.board_exists() and result.board_created
    assert read_json(canonical / ".supervisor" / "ledger" / "board.json")["epoch"] == "e1"
    assert queue.list() == []  # bootstrap never creates cards


def test_bootstrap_is_idempotent(tmp_path):
    ctx, env, _, queue = setup(tmp_path)
    runner = Recorder()
    run(ctx, env, queue, runner)
    head = gitops.head(ctx.canonical)
    runner.calls.clear()

    result = run(ctx, env, queue, runner)

    assert result.ok and not result.board_created
    assert gitops.head(ctx.canonical) == head  # no new commit, no new epoch
    assert runner.calls == []  # config already migrated: no hermes process


def test_shim_and_env_lines(tmp_path):
    ctx, env, _, queue = setup(tmp_path)
    home = ctx.hermes_home
    (home / "bin").mkdir()
    (home / "bin" / "tirith").write_text("scanner")  # Hermes's own binary (A6)
    (home / ".env").write_text("OPENROUTER_API_KEY=sk-test\nAUTORESEARCH_CLI=/old/path\nOTHER=1\n")

    run(ctx, env, queue)
    run(ctx, env, queue)

    shim = home / "bin" / "autoresearch"
    assert shim.read_text() == (
        f'#!/bin/sh\nexec "{home}/local/venv/bin/python" -m autoresearch "$@"\n'
    )
    assert shim.stat().st_mode & stat.S_IXUSR
    assert (home / "bin" / "tirith").read_text() == "scanner"
    lines = (home / ".env").read_text().splitlines()
    assert lines == [
        "OPENROUTER_API_KEY=sk-test",
        f"AUTORESEARCH_CLI={shim}",
        "OTHER=1",
        f"HERMES_WRITE_SAFE_ROOT={ctx.canonical}",
    ]


def test_config_migrations_apply_always_rows_and_respect_tuned_values(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "kanban": {"max_in_progress": 5, "default_assignee": "default"},
                "approvals": {"mode": "on"},
            }
        )
    )
    runner = Recorder()

    commands = config_migrations.apply(runner, home)

    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["kanban"]["max_in_progress"] == 5  # missing-only: the installer tuned it
    assert "default_assignee" not in config["kanban"]
    assert config["approvals"]["mode"] == "off"
    assert config["skills"]["create_dir"] == str(home / "local" / "skills-staging")
    assert config["terminal"]["env_passthrough"] == config_migrations.ENV_PASSTHROUGH
    assert ["config", "unset", "kanban.default_assignee"] in commands
    assert config_migrations.pending(home) == []


def test_shipped_config_agrees_with_the_migration_table(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text((AUTORESEARCH_DIR / "config.yaml").read_text())
    remaining = config_migrations.pending(home)
    # Only the per-host value is missing from the shipped file.
    assert remaining == [
        ["config", "set", "skills.create_dir", str(home / "local" / "skills-staging")]
    ]


def test_bootstrap_clones_an_existing_remote(tmp_path):
    ctx, env, _, queue = setup(tmp_path)
    run(ctx, env, queue)
    head = gitops.head(ctx.canonical)
    # A new host: same remote, empty home, the board is gone.
    new_home = tmp_path / "profiles2" / "autoresearch"
    new_home.mkdir(parents=True)
    new_ctx = RunContext(new_home, new_home / "workspace", None, None, None)
    new_queue = FileQueue(new_home / "board")

    result = run(new_ctx, env | {"HERMES_HOME": str(new_home)}, new_queue)

    assert result.ok and result.board_created
    assert gitops.git(new_ctx.canonical, "rev-parse", "HEAD~1") == head  # + new epoch commit
    assert read_json(new_ctx.canonical / ".supervisor" / "ledger" / "board.json")["epoch"] == "e2"


def test_bootstrap_without_a_remote_fails_on_a_fresh_workspace(tmp_path):
    ctx, env, _, queue = setup(tmp_path, remote=False)
    result = run(ctx, env, queue)
    assert not result.ok and "AUTORESEARCH_WORKSPACE_REMOTE" in result.error


def test_bootstrap_failure_in_a_migration_is_reported(tmp_path):
    ctx, env, _, queue = setup(tmp_path)

    def broken(argv, home):
        raise OSError("hermes not found")

    result = run(ctx, env, queue, broken)
    assert not result.ok and "hermes not found" in result.error


def test_bootstrap_uses_doctrine_when_present(tmp_path):
    ctx, env, _, queue = setup(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "games").symlink_to(AUTORESEARCH_DIR / "games")
    (repo / "doctrine").mkdir()
    (repo / "doctrine" / "research-agent.md").write_text("# Real doctrine\n")
    result = run(ctx, env, queue, repo_root=repo)
    assert result.ok and (ctx.canonical / "AGENTS.md").read_text() == "# Real doctrine\n"


def test_existing_canonical_gets_missing_gitignore_lines(tmp_path):
    ctx, env, _, queue = setup(tmp_path)
    run(ctx, env, queue)
    (ctx.canonical / ".gitignore").write_text(".worktrees/\n")
    gitops.commit_all(ctx.canonical, "lose a gitignore line")

    run(ctx, env, queue)

    assert ".supervisor/runtime/" in (ctx.canonical / ".gitignore").read_text()
    assert gitops.git(ctx.canonical, "status", "--porcelain") == ""
    assert os.path.exists(ctx.canonical / ".git")

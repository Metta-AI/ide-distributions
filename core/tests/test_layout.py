from pathlib import Path

import pytest

from research_core import layout


def test_folder_name_replaces_hyphens():
    assert layout.folder_name_for("cow_a-b-c") == "cow_a_b_c"


def test_folder_name_rejects_other_characters():
    with pytest.raises(ValueError):
        layout.folder_name_for("cow/evil")
    with pytest.raises(ValueError):
        layout.folder_name_for("")


def test_from_env_prefers_ide_workspace(tmp_path):
    ws = layout.from_env(
        {"IDE_WORKSPACE": str(tmp_path / "w"), "HERMES_REAL_HOME": str(tmp_path / "h")}
    )
    assert ws.root == tmp_path / "w"
    assert ws.venv == tmp_path / "w" / "tools" / "venv"
    assert ws.venv_python == tmp_path / "w" / "tools" / "venv" / "bin" / "python"


def test_from_env_falls_back_to_hermes_real_home(tmp_path):
    ws = layout.from_env({"HERMES_REAL_HOME": str(tmp_path / "h")})
    assert ws.root == tmp_path / "h" / "workspace"


def test_from_env_falls_back_to_home():
    ws = layout.from_env({})
    assert ws.root == Path.home() / ".hermes" / "workspace"


def test_venv_override(tmp_path):
    ws = layout.from_env(
        {"IDE_WORKSPACE": str(tmp_path), "IDE_WORKSPACE_ENV": str(tmp_path / "pre")}
    )
    assert ws.venv == tmp_path / "pre"


def test_coworld_paths(tmp_path):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path)})
    cw = ws.coworld("cow_pudge-basic")
    assert cw.root == tmp_path / "cow_pudge_basic"
    assert cw.folder_name == "cow_pudge_basic"
    for path in (cw.code, cw.mixin, cw.source, cw.worktrees, cw.documents, cw.state, cw.store_path):
        assert cw.root in path.parents
    assert cw.mixin == cw.code / "mixin"
    assert cw.worktree_for_task(3) == cw.worktrees / "task-3"
    assert cw.worktree_for_change(2) == cw.worktrees / "orch-2"
    assert ws.store_path == tmp_path / ".state" / "store.db"


def test_ensure_dirs(tmp_path):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "w")})
    ws.ensure_dirs()
    assert ws.tools.is_dir() and ws.state.is_dir()
    cw = ws.coworld("cow_x")
    cw.ensure_dirs()
    assert cw.worktrees.is_dir() and cw.documents.is_dir() and cw.state.is_dir()
    assert not cw.code.exists()

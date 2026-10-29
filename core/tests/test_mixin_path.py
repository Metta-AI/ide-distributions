"""Mixin commands run with the workspace environment first on PATH."""

import os

from research_core import mixin


def test_with_workspace_bin_prepends_the_venv(tmp_path):
    bin_dir = tmp_path / "ws" / "tools" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    env = {"IDE_WORKSPACE": str(tmp_path / "ws"), "PATH": "/usr/bin"}
    merged = mixin.with_workspace_bin(env)
    assert merged["PATH"] == f"{bin_dir}{os.pathsep}/usr/bin"
    assert merged["IDE_WORKSPACE"] == env["IDE_WORKSPACE"]


def test_with_workspace_bin_leaves_path_alone_without_a_venv(tmp_path):
    env = {"IDE_WORKSPACE": str(tmp_path / "ws"), "PATH": "/usr/bin"}
    assert mixin.with_workspace_bin(env)["PATH"] == "/usr/bin"

from ide_research_core.runctx import from_env, runtime_dir


def test_defaults_canonical_under_hermes_home(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert ctx.canonical == tmp_path / "workspace"
    assert ctx.worker is None
    assert ctx.profile == tmp_path.name


def test_workspace_and_worker_from_env(tmp_path):
    env = {
        "HERMES_HOME": str(tmp_path / "researcher-1"),
        "RESEARCH_WORKSPACE": str(tmp_path / "clone"),
        "HERMES_KANBAN_WORKSPACE": str(tmp_path / "clone" / ".worktrees" / "card-1"),
    }
    ctx = from_env(env)
    assert ctx.canonical == tmp_path / "clone"
    assert ctx.worker == tmp_path / "clone" / ".worktrees" / "card-1"
    assert ctx.profile == "researcher-1"


def test_runtime_dir_is_untracked_state_in_the_clone(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path), "RESEARCH_WORKSPACE": str(tmp_path / "c")})
    assert runtime_dir(ctx) == tmp_path / "c" / ".supervisor" / "runtime"

import json

from autoresearch.runctx import from_env, ledger_dir, runtime_dir


def test_defaults_canonical_under_hermes_home(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert ctx.hermes_home == tmp_path
    assert ctx.canonical == tmp_path / "workspace"
    assert ctx.worker is None and ctx.card_id is None and ctx.run_id is None


def test_override_and_worker_env(tmp_path):
    env = {
        "HERMES_HOME": str(tmp_path),
        "AUTORESEARCH_WORKSPACE": str(tmp_path / "elsewhere"),
        "HERMES_KANBAN_WORKSPACE": str(tmp_path / "wt"),
        "HERMES_KANBAN_TASK": "t_1",
        "HERMES_KANBAN_RUN": "7",
    }
    ctx = from_env(env)
    assert ctx.canonical == tmp_path / "elsewhere"
    assert ctx.worker == tmp_path / "wt"
    assert (ctx.card_id, ctx.run_id) == ("t_1", "7")


def test_dirs(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert runtime_dir(ctx) == tmp_path / "workspace/.supervisor/runtime"
    assert ledger_dir(ctx) == tmp_path / "workspace/.supervisor/ledger"


def test_serial_fallback_reads_file(tmp_path):
    runtime = tmp_path / "workspace/.supervisor/runtime"
    runtime.mkdir(parents=True)
    (runtime / "serial_current.json").write_text(
        json.dumps({"task_id": "t_9", "run_id": "3", "worker": str(tmp_path / "wt9")})
    )
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert ctx.card_id == "t_9" and ctx.run_id == "3"
    assert ctx.worker == tmp_path / "wt9"


def test_env_task_beats_serial_file(tmp_path):
    runtime = tmp_path / "workspace/.supervisor/runtime"
    runtime.mkdir(parents=True)
    (runtime / "serial_current.json").write_text(json.dumps({"task_id": "t_9"}))
    ctx = from_env({"HERMES_HOME": str(tmp_path), "HERMES_KANBAN_TASK": "t_1"})
    assert ctx.card_id == "t_1"

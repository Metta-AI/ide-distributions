"""Profile preparation against a fake ``hermes`` executable and a fake ``uv``."""

import hashlib
import json
import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from research_core import layout, prepare

REPO = Path(__file__).resolve().parents[2]
CORE_SRC = REPO / "core" / "src"


def _parse_set_value(text: str):
    """What ``hermes config set`` stores for a command-line value: list and mapping literals
    are parsed, scalar words and numbers coerced, anything else kept as text."""
    if text[:1] in ("[", "{"):
        return json.loads(text)
    if text in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[text]
    for convert in (int, float):
        try:
            return convert(text)
        except ValueError:
            pass
    return text


class FakeHermes:
    """Stands in for the ``hermes`` executable and for ``uv``.

    Answers ``config get`` from an in-memory dotted-key dict, applies ``config set`` and
    ``config unset`` to it the way Hermes does (a missing key is "Config key not set", exit 1),
    records every ``cron resume`` and every argv it was called with.
    """

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.calls: list[tuple[list[str], dict]] = []
        self.resumed: list[str] = []
        self.uv_result = CompletedProcess(["uv"], 0, "", "")

    def run(self, argv, **kwargs):
        argv = [str(part) for part in argv]
        self.calls.append((argv, kwargs))
        if argv[0] == "uv":
            return self.uv_result
        exe, *rest = argv
        assert exe == "hermes", argv
        if rest[:2] == ["config", "get"]:
            key = rest[2]
            assert rest[3:] == ["--json"], rest
            if key not in self.config:
                return CompletedProcess(argv, 1, "", f"Config key not set: {key}\n")
            return CompletedProcess(argv, 0, json.dumps(self.config[key]) + "\n", "")
        if rest[:2] == ["config", "set"]:
            key, value = rest[2], rest[3]
            self.config[key] = _parse_set_value(value)
            return CompletedProcess(argv, 0, f"✓ Set {key}\n", "")
        if rest[:2] == ["config", "unset"]:
            key = rest[2]
            if key not in self.config:
                return CompletedProcess(argv, 1, "", f"Config key not set: {key}\n")
            del self.config[key]
            return CompletedProcess(argv, 0, f"✓ Unset {key}\n", "")
        if rest[:2] == ["cron", "resume"]:
            self.resumed.append(rest[2])
            return CompletedProcess(argv, 0, f"Resumed job: {rest[2]}\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    def hermes_calls(self):
        return [argv for argv, _ in self.calls if argv[0] == "hermes"]

    def sets(self):
        """The last value passed to ``config set`` per key."""
        return {argv[3]: argv[4] for argv in self.hermes_calls() if argv[1:3] == ["config", "set"]}

    def unsets(self):
        return [argv[3] for argv in self.hermes_calls() if argv[1:3] == ["config", "unset"]]


@pytest.fixture
def homes(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    env = {
        "HERMES_HOME": str(profile),
        "HERMES_REAL_HOME": str(tmp_path / "host"),
        "IDE_WORKSPACE": str(tmp_path / "workspace"),
        "PATH": os.environ.get("PATH", ""),
    }
    return profile, env


def run_prepare(fake, env, role="orchestrator", activate=False):
    prepare.prepare(
        source=REPO / role, hermes="hermes", activate_watcher=activate, env=env, run=fake.run
    )


def read_jobs(profile):
    return json.loads((profile / "cron" / "jobs.json").read_text())


# --- keys -------------------------------------------------------------------------------------


def test_protected_keys_for_role_substitutes_the_role():
    orchestrator = prepare.protected_keys_for("orchestrator")
    researcher = prepare.protected_keys_for("researcher")
    assert orchestrator["plugins.enabled"] == ["orchestrator"]
    assert researcher["plugins.enabled"] == ["researcher"]
    assert researcher["platform_toolsets.api_server"] == ["hermes-api-server", "ide-researcher"]
    assert researcher["known_plugin_toolsets.cron"] == ["ide-researcher"]
    assert orchestrator["approvals.mode"] == "off"
    assert orchestrator["memory.memory_enabled"] is False
    assert orchestrator["tools.tool_search.enabled"] == "off"
    assert orchestrator["cron.catch_up_missed"] is True
    # Compression protection is Orchestrator only (design F.5).
    assert orchestrator["compression.protect_first_n"] == 6
    assert orchestrator["compression.protect_last_n"] == 30
    assert orchestrator["compression.threshold"] == 0.6
    assert orchestrator["compression.target_ratio"] == 0.3
    assert not any(key.startswith("compression.") for key in researcher)
    assert "<role>" not in json.dumps(orchestrator) + json.dumps(researcher)


def test_prepare_rejects_unknown_role(tmp_path, homes):
    profile, env = homes
    source = tmp_path / "checkout" / "supervisor"
    source.mkdir(parents=True)
    with pytest.raises(prepare.PrepareError, match="supervisor"):
        prepare.prepare(source=source, hermes="hermes", activate_watcher=False, env=env)


def test_prepare_requires_hermes_home():
    with pytest.raises(prepare.PrepareError, match="HERMES_HOME"):
        prepare.prepare(
            source=REPO / "orchestrator", hermes="hermes", activate_watcher=False, env={}
        )


# --- the core path and the summary ------------------------------------------------------------


def test_prepare_writes_core_path(homes):
    profile, env = homes
    run_prepare(FakeHermes(), env)
    assert (profile / "local" / "core-path.txt").read_text().strip() == str(CORE_SRC)


def test_prepare_prints_one_json_summary_line(homes, capsys):
    profile, env = homes
    run_prepare(FakeHermes(), env)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["role"] == "orchestrator"
    assert summary["workspace"] == env["IDE_WORKSPACE"]
    assert summary["venv"] == str(Path(env["IDE_WORKSPACE"]) / "tools" / "venv")
    assert isinstance(summary["actions"], list) and summary["actions"]


def test_prepare_runs_every_hermes_command_in_the_profile_home(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env, activate=True)
    hermes_calls = [kwargs for argv, kwargs in fake.calls if argv[0] == "hermes"]
    assert hermes_calls
    for kwargs in hermes_calls:
        assert kwargs["env"]["HERMES_HOME"] == str(profile)
        assert kwargs["env"]["PATH"] == env["PATH"]


# --- config keys ------------------------------------------------------------------------------


def test_prepare_sets_protected_keys_and_unsets_retired(homes):
    profile, env = homes
    fake = FakeHermes(
        {
            "approvals.mode": "smart",
            "approvals.deny": ["rm -rf /"],
            "agent.max_turns": 40,
            "agent.run_budget_seconds": 5400,
            "timeouts": {"tools": {"sequential_call": 420}},
            "kanban": {"auto_decompose": False},
            "skills.external_dirs": ["/kept/elsewhere", "../../games"],
            "terminal.timeout": 1800,
        }
    )
    run_prepare(fake, env)

    for key, value in prepare.protected_keys_for("orchestrator").items():
        assert fake.config[key] == value, key
    for key in ("approvals.deny", "agent.max_turns", "agent.run_budget_seconds", "timeouts"):
        assert key not in fake.config, key
    assert "kanban" not in fake.config
    assert fake.config["skills.external_dirs"] == [
        str(CORE_SRC / "research_core" / "skills" / "common"),
        str(CORE_SRC / "research_core" / "skills" / "orchestrator"),
    ]
    assert fake.config["terminal.timeout"] == 1800, "an owner's value is kept"

    sets = fake.sets()
    assert sets["approvals.mode"] == "off", "a string value goes verbatim"
    assert json.loads(sets["plugins.enabled"]) == ["orchestrator"], "structured values are JSON"
    assert sets["memory.memory_enabled"] == "false"
    assert set(fake.unsets()) >= {"approvals.deny", "agent.max_turns", "timeouts", "kanban"}


def test_prepare_only_sets_keys_whose_value_differs(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env)
    first = len(fake.sets())
    assert first >= len(prepare.protected_keys_for("orchestrator"))
    fake.calls.clear()
    run_prepare(fake, env)
    assert fake.sets() == {}, "a second run finds every protected key in place"
    # The retired keys are still probed (Hermes reports "not set") but nothing fails.
    assert set(fake.unsets()) == set(prepare.plain_retired_keys())


def test_prepare_researcher_uses_its_role_and_has_no_cron(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env, role="researcher", activate=True)
    assert fake.config["plugins.enabled"] == ["researcher"]
    assert fake.config["platform_toolsets.api_server"] == ["hermes-api-server", "ide-researcher"]
    assert "compression.protect_first_n" not in fake.config
    assert fake.config["skills.external_dirs"][1].endswith("/skills/researcher")
    assert not (profile / "cron").exists()
    assert fake.resumed == []
    assert (profile / ".softmax-prepared").exists()


def test_prepare_reports_a_failed_hermes_command_with_its_output(homes):
    profile, env = homes

    def failing_run(argv, **kwargs):
        if argv[0] == "uv":
            return CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["config", "get"]:
            return CompletedProcess(argv, 1, "", "Config key not set: x\n")
        return CompletedProcess(argv, 1, "partial stdout\n", "boom: config is unreadable\n")

    with pytest.raises(prepare.PrepareError) as info:
        prepare.prepare(
            source=REPO / "orchestrator",
            hermes="hermes",
            activate_watcher=False,
            env=env,
            run=failing_run,
        )
    message = str(info.value)
    assert "exit code 1" in message and "boom: config is unreadable" in message
    assert "partial stdout" in message


# --- the watcher job --------------------------------------------------------------------------


def shipped_watcher():
    jobs = json.loads((REPO / "orchestrator" / "cron" / "jobs.json").read_text())["jobs"]
    return next(job for job in jobs if job["id"] == "watcher")


def test_prepare_reconciles_watcher_job_keeps_pause_state(homes):
    profile, env = homes
    stale = dict(shipped_watcher())
    stale.update(
        {
            "script": "supervisor.py",
            "no_agent": False,
            "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
            "schedule_display": "every 5m",
            "enabled_toolsets": ["terminal"],
            "enabled": False,
            "state": "paused",
            "paused_at": "2026-10-20T10:00:00-07:00",
            "paused_reason": "the owner paused it",
            "last_run_at": "2026-10-19T09:00:00-07:00",
            "last_status": "ok",
            "failure_streak": 2,
        }
    )
    foreign = {"id": "nightly", "name": "nightly", "prompt": "hello", "enabled": True}
    (profile / "cron").mkdir()
    (profile / "cron" / "jobs.json").write_text(
        json.dumps({"jobs": [foreign, stale], "updated_at": "2026-10-20T10:00:00-07:00"})
    )

    run_prepare(FakeHermes(), env, activate=False)

    payload = read_jobs(profile)
    assert [job["id"] for job in payload["jobs"]] == ["nightly", "watcher"]
    assert payload["jobs"][0] == foreign
    assert payload["updated_at"] == "2026-10-20T10:00:00-07:00"
    watcher = payload["jobs"][1]
    shipped = shipped_watcher()
    for field in prepare.WATCHER_JOB_FIELDS:
        assert watcher[field] == shipped[field], field
    assert watcher["enabled"] is False and watcher["state"] == "paused"
    assert watcher["paused_at"] == "2026-10-20T10:00:00-07:00"
    assert watcher["paused_reason"] == "the owner paused it"
    assert watcher["last_run_at"] == "2026-10-19T09:00:00-07:00"
    assert watcher["failure_streak"] == 2
    assert not list((profile / "cron").glob("*.pending"))


def test_prepare_adds_a_missing_watcher_job_paused(homes):
    profile, env = homes
    foreign = {"id": "nightly", "name": "nightly", "prompt": "hello", "enabled": True}
    (profile / "cron").mkdir()
    (profile / "cron" / "jobs.json").write_text(json.dumps({"jobs": [foreign]}))

    run_prepare(FakeHermes(), env)

    jobs = read_jobs(profile)["jobs"]
    assert [job["id"] for job in jobs] == ["nightly", "watcher"]
    assert jobs[1] == shipped_watcher()
    assert jobs[1]["enabled"] is False and jobs[1]["state"] == "paused"


def test_prepare_creates_the_cron_store_when_missing(homes):
    profile, env = homes
    run_prepare(FakeHermes(), env)
    assert read_jobs(profile)["jobs"] == [shipped_watcher()]


def test_prepare_accepts_a_bare_job_list(homes):
    profile, env = homes
    (profile / "cron").mkdir()
    (profile / "cron" / "jobs.json").write_text(json.dumps([shipped_watcher()]))
    run_prepare(FakeHermes(), env)
    assert read_jobs(profile)["jobs"] == [shipped_watcher()]


def test_prepare_activates_watcher_once(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env, activate=False)
    assert fake.resumed == [], "no activation without the flag"
    assert (profile / ".softmax-prepared").exists()

    (profile / ".softmax-prepared").unlink()
    run_prepare(fake, env, activate=True)
    assert fake.resumed == ["watcher"]
    assert (profile / ".softmax-prepared").exists()

    run_prepare(fake, env, activate=True)
    assert fake.resumed == ["watcher"], "the marker stops a second activation"


def test_prepare_resumes_after_the_job_exists(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env, activate=True)
    hermes_calls = fake.hermes_calls()
    resume_index = hermes_calls.index(["hermes", "cron", "resume", "watcher"])
    assert resume_index == len(hermes_calls) - 1
    assert read_jobs(profile)["jobs"][0]["id"] == "watcher"


# --- the workspace environment ----------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "uv.lock").write_text("version = 1\n")
    return root


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ensure_workspace_env_syncs_when_stamp_differs(tmp_path, checkout):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    ws.ensure_dirs()
    fake = FakeHermes()

    assert prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={"PATH": "/bin"}) is True
    ((argv, kwargs),) = fake.calls
    assert argv == ["uv", "sync", "--frozen", "--no-dev", "--project", str(checkout)]
    assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"] == str(ws.venv)
    assert kwargs["env"]["PATH"] == "/bin"
    assert (ws.venv / ".lock-sha").read_text().strip() == sha(checkout / "uv.lock")

    fake.calls.clear()
    assert prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={}) is False
    assert fake.calls == [], "a matching stamp means no sync"

    (checkout / "uv.lock").write_text("version = 2\n")
    assert prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={}) is True
    assert (ws.venv / ".lock-sha").read_text().strip() == sha(checkout / "uv.lock")


def test_ensure_workspace_env_prebuilt_mismatch_raises(tmp_path, checkout):
    prebuilt = tmp_path / "prebuilt"
    prebuilt.mkdir()
    ws = layout.from_env(
        {"IDE_WORKSPACE": str(tmp_path / "ws"), "IDE_WORKSPACE_ENV": str(prebuilt)}
    )
    fake = FakeHermes()

    with pytest.raises(prepare.PrepareError) as info:
        prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={})
    assert str(prebuilt) in str(info.value) and str(checkout / "uv.lock") in str(info.value)
    assert fake.calls == [], "a prebuilt environment is never synced"

    (prebuilt / ".lock-sha").write_text("0" * 64 + "\n")
    with pytest.raises(prepare.PrepareError):
        prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={})

    (prebuilt / ".lock-sha").write_text(sha(checkout / "uv.lock") + "\n")
    assert prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={}) is False
    assert fake.calls == []


def test_ensure_workspace_env_symlinked_venv_is_prebuilt(tmp_path, checkout):
    """Hosting may link tools/venv to a read-only image environment (Figure 2)."""
    image_env = tmp_path / "image-env"
    image_env.mkdir()
    (image_env / ".lock-sha").write_text(sha(checkout / "uv.lock") + "\n")
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    ws.ensure_dirs()
    ws.venv.symlink_to(image_env)
    fake = FakeHermes()
    assert prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={}) is False
    assert fake.calls == []


def test_ensure_workspace_env_reports_uv_failure_with_output(tmp_path, checkout):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    fake = FakeHermes()
    fake.uv_result = CompletedProcess(["uv"], 2, "resolving...\n", "error: No solution found\n")
    with pytest.raises(prepare.PrepareError) as info:
        prepare.ensure_workspace_env(checkout, ws, run=fake.run, env={})
    message = str(info.value)
    assert "exit code 2" in message and "No solution found" in message and "resolving" in message
    assert not (ws.venv / ".lock-sha").exists()


def test_ensure_workspace_env_requires_uv(tmp_path, checkout):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})

    def no_uv(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(prepare.PrepareError, match="uv"):
        prepare.ensure_workspace_env(checkout, ws, run=no_uv, env={})


def test_prepare_passes_the_release_checkout_to_uv(homes):
    profile, env = homes
    fake = FakeHermes()
    run_prepare(fake, env)
    (uv_call,) = [argv for argv, _ in fake.calls if argv[0] == "uv"]
    assert uv_call[-1] == str(REPO)
    venv = Path(env["IDE_WORKSPACE"]) / "tools" / "venv"
    assert (venv / ".lock-sha").read_text().strip() == sha(REPO / "uv.lock")

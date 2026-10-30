import json
import time
from pathlib import Path

import pytest

from research_core import mixin

GOOD = """\
---
coworld: fakegame
coworld_version: 0.1.8
commands:
  build: python3 mixin/tools/build.py --out dist
  check: python3 mixin/tools/check.py dist/policy.bas
  upload: python3 mixin/tools/upload.py dist/policy.bas
  evaluate: python3 mixin/tools/evaluate.py --results "$RESULTS"
metrics:
  - {name: win, direction: up, role: primary, min_effect: 0.05, minimum_episodes: 100}
  - {name: deaths, direction: down, role: guardrail, tolerance: 0.5}
artifact: {kind: file, path: dist/policy.bas}
evidence_types: [replay-segment, {name: seat-log, resolver: mixin/tools/seat_log.py}]
toolchains:
  - {kind: nim, version: "2.0.8", url: https://example.invalid/nim.tar.xz, sha256: abc123}
cost_per_episode: 0.25
---
# Fakegame

Prose tables stay.

| Tool | Use |
|---|---|
| build | builds |
"""


def write_mixin(root: Path, text: str = GOOD, *, skills: bool = True) -> Path:
    mixin_dir = root / "code" / "mixin"
    mixin_dir.mkdir(parents=True)
    (mixin_dir / "MIXIN.md").write_text(text)
    if skills:
        (mixin_dir / "skills" / "survey").mkdir(parents=True)
    return mixin_dir


def test_load_parses_front_matter_and_body(tmp_path):
    mixin_dir = write_mixin(tmp_path)
    loaded = mixin.load(mixin_dir)
    assert loaded.coworld == "fakegame"
    assert loaded.coworld_version == "0.1.8"
    assert loaded.commands["build"] == "python3 mixin/tools/build.py --out dist"
    assert set(loaded.commands) == {"build", "check", "upload", "evaluate"}
    assert [m["name"] for m in loaded.metrics] == ["win", "deaths"]
    assert loaded.metrics[0]["minimum_episodes"] == 100
    assert loaded.artifact == {"kind": "file", "path": "dist/policy.bas"}
    assert loaded.evidence_types == [
        {"name": "replay-segment"},
        {"name": "seat-log", "resolver": "mixin/tools/seat_log.py"},
    ]
    assert loaded.toolchains[0]["kind"] == "nim"
    assert loaded.cost_per_episode == 0.25
    assert loaded.skills_dir == mixin_dir / "skills"
    assert loaded.path == mixin_dir
    assert loaded.body.startswith("# Fakegame\n")
    assert "| build | builds |" in loaded.body
    as_dict = loaded.to_dict()
    json.dumps(as_dict)
    assert as_dict["skills_dir"] == str(mixin_dir / "skills")


def test_load_without_skills_dir_and_optional_fields(tmp_path):
    text = (
        "---\ncoworld: fakegame\ncommands: {build: make, check: make check}\n"
        "artifact: {kind: image, path: Dockerfile}\n---\n"
    )
    loaded = mixin.load(write_mixin(tmp_path, text, skills=False))
    assert loaded.skills_dir is None
    assert loaded.coworld_version is None
    assert loaded.metrics == [] and loaded.evidence_types == [] and loaded.toolchains == []
    assert loaded.cost_per_episode is None
    assert loaded.body == ""


def test_load_reports_every_problem_at_once(tmp_path):
    text = (
        "---\ncommands: {check: make check}\nartifact: {kind: tarball}\n"
        "metrics: [{name: win, direction: sideways}]\ncost_per_episode: -1\n---\nbody\n"
    )
    with pytest.raises(mixin.MixinError) as info:
        mixin.load(write_mixin(tmp_path, text))
    problems = info.value.problems
    assert "coworld: required" in problems
    assert "commands.build: required" in problems
    assert any(p.startswith("artifact.kind:") for p in problems)
    assert any(p.startswith("artifact.path:") for p in problems)
    assert any(p.startswith("metrics.win.direction:") for p in problems)
    assert any(p.startswith("cost_per_episode:") for p in problems)
    assert len(problems) == 6
    assert "coworld: required" in str(info.value)


def test_metrics_mapping_and_list_normalise_alike(tmp_path):
    as_list = (
        "---\ncoworld: g\ncommands: {build: b, check: c}\nartifact: {kind: file, path: p}\n"
        "metrics:\n  - {name: win, direction: up, role: primary}\n"
        "  - {name: deaths, direction: down}\n---\n"
    )
    as_mapping = (
        "---\ncoworld: g\ncommands: {build: b, check: c}\nartifact: {kind: file, path: p}\n"
        "metrics:\n  win: {direction: up, role: primary}\n  deaths: down\n---\n"
    )
    from_list = mixin.load(write_mixin(tmp_path / "list", as_list)).metrics
    from_mapping = mixin.load(write_mixin(tmp_path / "mapping", as_mapping)).metrics
    assert from_list == from_mapping
    assert from_list == [
        {"name": "win", "direction": "up", "role": "primary"},
        {"name": "deaths", "direction": "down"},
    ]


def test_load_refuses_missing_file_and_missing_front_matter(tmp_path):
    with pytest.raises(mixin.MixinError, match="MIXIN.md not found"):
        mixin.load(tmp_path / "nowhere")
    with pytest.raises(mixin.MixinError, match="no front matter"):
        mixin.load(write_mixin(tmp_path / "plain", "# Just prose\n"))
    with pytest.raises(mixin.MixinError, match="not closed"):
        mixin.load(write_mixin(tmp_path / "open", "---\ncoworld: g\n# never closed\n"))
    with pytest.raises(mixin.MixinError, match="not valid YAML"):
        mixin.load(write_mixin(tmp_path / "bad", "---\ncoworld: [\n---\n"))


def make_mixin(commands: dict[str, str], root: Path) -> mixin.Mixin:
    return mixin.Mixin(
        coworld="g",
        coworld_version=None,
        commands=commands,
        metrics=[],
        artifact={"kind": "file", "path": "out"},
        evidence_types=[],
        toolchains=[],
        cost_per_episode=None,
        skills_dir=None,
        path=root / "mixin",
        body="",
    )


def test_run_command_success_captures_output_and_log(tmp_path):
    spec = make_mixin({"build": "echo built $IDE_TEST_VALUE; echo warn 1>&2; pwd"}, tmp_path)
    result = mixin.run_command(
        spec,
        "build",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "IDE_TEST_VALUE": "ok"},
        budget_s=30,
        log_dir=tmp_path / "logs",
    )
    assert result.finished is True and result.exit_code == 0 and result.ok
    assert result.resume_token is None
    assert result.name == "build" and result.command.startswith("echo built")
    assert "built ok" in result.output_tail and "warn" in result.output_tail
    assert str(tmp_path.resolve()) in result.output_tail
    assert result.log_path.parent == tmp_path / "logs"
    assert result.log_path.name.startswith("build-") and result.log_path.suffix == ".log"
    log_text = result.log_path.read_text()
    assert log_text.startswith("$ echo built")
    assert "built ok" in log_text
    assert result.duration_s >= 0
    json.dumps(result.to_dict())


def test_run_command_failure_reports_exit_code(tmp_path):
    spec = make_mixin({"check": "echo boom 1>&2; exit 3"}, tmp_path)
    result = mixin.run_command(
        spec, "check", cwd=tmp_path, env=None, budget_s=30, log_dir=tmp_path / "logs"
    )
    assert result.finished is True and result.exit_code == 3 and not result.ok
    assert "boom" in result.output_tail
    assert (tmp_path / "logs" / (result.log_path.name + ".exit")).read_text().strip() == "3"


def test_run_command_unknown_name_is_refused(tmp_path):
    spec = make_mixin({"build": "true"}, tmp_path)
    with pytest.raises(mixin.MixinError, match="no 'evaluate' command"):
        mixin.run_command(spec, "evaluate", cwd=tmp_path, env=None, log_dir=tmp_path)


def test_run_command_over_budget_backgrounds_then_resume_reports_exit(tmp_path):
    spec = make_mixin({"build": "echo start; sleep 3; echo done"}, tmp_path)
    started = mixin.run_command(
        spec, "build", cwd=tmp_path, env=None, budget_s=1, log_dir=tmp_path / "logs"
    )
    assert started.finished is False and started.exit_code is None
    assert started.pid > 0 and started.resume_token
    token = json.loads(started.resume_token)
    assert token["pid"] == started.pid and token["log"] == str(started.log_path)
    assert "start" in started.output_tail

    # Still running right after the budget: resume says so and keeps the token.
    probe = mixin.resume(started.resume_token)
    assert probe.finished is False and probe.resume_token == started.resume_token

    deadline = time.monotonic() + 15
    while not (final := mixin.resume(started.resume_token)).finished:
        assert time.monotonic() < deadline, "backgrounded command never finished"
        time.sleep(0.2)
    assert final.exit_code == 0 and final.ok
    assert final.resume_token is None
    assert "done" in final.output_tail
    assert final.log_path == started.log_path
    assert final.name == "build" and final.command == spec.commands["build"]
    assert 2.5 <= final.duration_s < 15


def test_resume_refuses_garbage(tmp_path):
    with pytest.raises(mixin.MixinError, match="not a resume token"):
        mixin.resume("{}")
    with pytest.raises(mixin.MixinError, match="not a resume token"):
        mixin.resume("not json")

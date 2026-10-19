import json
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator.plugin import TOOLS
from orchestrator.research import Research, ResearchError


def test_manifest_advertises_registered_tools():
    manifest = Path(__file__).resolve().parents[1] / "plugins/orchestrator/plugin.yaml"
    assert set(yaml.safe_load(manifest.read_text())["provides_tools"]) == set(TOOLS)


def test_subprocess_contract_and_refusal(shared_fixture):
    research = Research(shared_fixture)
    assert research.records_find("Experiment", {"status": "reviewed"}) == {
        "args": ["records", "find", "Experiment", "status=reviewed"]
    }
    assert research.workspace_integrate("repair/test")["args"] == [
        "workspace",
        "integrate",
        "--branch",
        "repair/test",
    ]
    with pytest.raises(ResearchError) as error:
        research.promotion_submit("refuse")
    assert error.value.envelope["error"]["reason"] == "baseline_not_champion"
    assert research.bootstrap_champion("V-starter")["args"] == [
        "promotion",
        "bootstrap",
        "--version",
        "V-starter",
    ]
    assert TOOLS["bootstrap_champion"][3] == ["version"]
    with pytest.raises(TypeError):
        research.bootstrap_champion()
    assert research.release_program("mine:v1")["args"] == [
        "promotion",
        "release",
        "--policy-version",
        "mine:v1",
    ]
    assert TOOLS["release_program"][3] == ["policy_version"]
    assert research.accept_champion()["args"] == ["promotion", "accept"]
    assert TOOLS["accept_champion"][2:] == ({}, [])


def test_publish_writes_exact_contract_files(shared_fixture):
    research = Research(shared_fixture)
    research.plan_write("A concrete plan\n")
    research.control_write("pause", "coach asked")
    assert (shared_fixture.workspace / "plan.md").read_text() == "A concrete plan\n"
    pause = json.loads((shared_fixture.workspace / "control/pause.json").read_text())
    assert pause["paused"] is True and pause["by"] == "orchestrator"
    assert pause["reason"] == "coach asked" and pause["at"]
    calls = [
        json.loads(line) for line in (shared_fixture.home / "calls.jsonl").read_text().splitlines()
    ]
    assert calls[0][-2:] == ["--path", "plan.md"]
    assert calls[1][-2:] == ["--path", "control/pause.json"]
    research.control_write("resume", "continue")
    assert (
        json.loads((shared_fixture.workspace / "control/pause.json").read_text())["paused"] is False
    )


def test_control_read_uses_canonical_main(context):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(context.workspace), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-m", "Initial")
    canonical = context.home / "canonical.git"
    git("clone", "--bare", str(context.workspace), str(canonical))
    context.env["RESEARCH_CANONICAL"] = str(canonical)
    research = Research(context)
    assert research.control_read() is None
    pause = context.workspace / "control/pause.json"
    pause.parent.mkdir()
    pause.write_text('{"paused": true}')
    git("add", "control/pause.json")
    git("commit", "-m", "Pause")
    git("push", str(canonical), "main")
    pause.write_text('{"paused": false}')
    assert research.control_read() == {"paused": True}

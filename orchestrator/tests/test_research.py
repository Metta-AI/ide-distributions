import json

import pytest

from orchestrator.research import Research, ResearchError


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

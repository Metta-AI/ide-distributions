import json
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator.plugin import GAME, TOOLS
from orchestrator.research import Research, ResearchError


def test_manifest_advertises_registered_tools():
    manifest = Path(__file__).resolve().parents[1] / "plugins/orchestrator/plugin.yaml"
    advertised = set(yaml.safe_load(manifest.read_text())["provides_tools"])
    # Shared tools registered beside TOOLS: ide_research_core.session_catalog and .host_capabilities.
    assert advertised == set(TOOLS) | {"capabilities", "host_capabilities"}


def test_subprocess_contract_and_refusal(shared_fixture):
    research = Research(shared_fixture)
    assert research.records_find("Experiment", {"status": "reviewed"}) == {
        "args": ["records", "find", "Experiment", "status=reviewed"]
    }
    with pytest.raises(ResearchError) as error:
        research.call("champion", "submit", "refuse")
    assert error.value.envelope["error"]["reason"] == "baseline_not_champion"
    assert research.call("champion", "bootstrap", "--version", "V-starter")["args"] == [
        "champion",
        "bootstrap",
        "--version",
        "V-starter",
    ]
    assert TOOLS["submit_requested_version"][3] == [
        "id",
        "run_id",
        "version",
        "strategy",
        "request",
        "league_id",
    ]
    assert TOOLS["bootstrap_champion"][3] == ["id", "run_id", "version", "league_id"]
    assert research.accept_champion("league_selected")["args"] == ["champion", "accept", "--league", "league_selected"]
    assert TOOLS["accept_champion"][2:] == ({"league_id": {"type": "string"}, "game": GAME}, ["league_id"])


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
    plan = context.workspace / "plan.md"
    plan.write_text("User constraint: no statistical champion submission.\n")
    git("add", "plan.md")
    git("commit", "-m", "Persist user constraint")
    git("push", str(canonical), "main")
    plan.write_text("Unpublished draft\n")
    assert Research(context).plan_read() == "User constraint: no statistical champion submission.\n"


def test_another_game_uses_its_own_workspace(shared_fixture):
    """``game`` points the shared CLI at that game's canonical repository and this profile's
    clone of it, made on first use; with no canonical repository there is nothing to clone."""
    host = shared_fixture.home.parent.parent
    shared_fixture.env.update(
        RESEARCH_GAME="gota",
        RESEARCH_CANONICAL=str(host / "canonical-gota.git"),
    )
    research = Research(shared_fixture)
    assert research.in_game(None) is research and research.in_game("gota") is research
    with pytest.raises(ResearchError) as error:
        research.records_find("Experiment", game="newgame")
    assert error.value.envelope["error"]["reason"] == "no_workspace"
    (host / "canonical-newgame.git").mkdir()
    (host / "canonical-newgame.git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert research.records_show("E-1", game="newgame")["args"] == ["records", "show", "E-1"]
    calls = [
        json.loads(line) for line in (shared_fixture.home / "calls.jsonl").read_text().splitlines()
    ]
    assert calls[-2:] == [["workspace", "bootstrap"], ["records", "show", "E-1"]]
    scoped = research.in_game("newgame").context
    assert scoped.env["RESEARCH_GAME"] == "newgame"
    assert scoped.env["RESEARCH_CANONICAL"] == str(host / "canonical-newgame.git")
    assert scoped.workspace == shared_fixture.home / "workspace-newgame"


def test_game_prepare_bootstraps_the_named_game(shared_fixture):
    assert Research(shared_fixture).game_prepare("pudge-wars-basic")["args"] == [
        "workspace",
        "bootstrap",
        "--game",
        "pudge-wars-basic",
    ]
    assert TOOLS["game_prepare"][3] == ["game"]


def test_orchestrator_seeds_a_shipped_game_without_a_researcher(tmp_path):
    """The orchestrator's own copy of a shipped game folder is enough to create its workspace."""
    from ide_research_core import workspace
    from ide_research_core.io import gitops

    bare, clone = tmp_path / "canonical-pudge-wars-basic.git", tmp_path / "workspace-pudge-wars-basic"
    actions = workspace.bootstrap(clone, bare, "pudge-wars-basic")
    assert actions[1] == f"seeded {bare} from games/pudge-wars-basic/seed"
    assert (clone / "strategy/policy.bas").is_file()
    assert gitops.git(bare, "for-each-ref", "refs/heads/main")


def test_status_lists_every_researcher_with_its_open_cards(tmp_path):
    from orchestrator.context import Context
    from orchestrator.service import researchers

    profiles = tmp_path / "profiles"
    for name, marker in (("orchestrator", False), ("researcher-1", True), ("researcher-2", True)):
        (profiles / name).mkdir(parents=True)
        (profiles / name / "config.yaml").write_text("{}")
        if marker:
            (profiles / name / "plugins" / "researcher_guard").mkdir(parents=True)
    cards = [
        {"id": "t_1", "assignee": "researcher-1", "status": "running"},
        {"id": "t_2", "assignee": "researcher-1", "status": "ready"},
        {"id": "t_3", "assignee": "researcher-1", "status": "done"},
    ]
    found = researchers(Context(profiles / "orchestrator", {}), cards)
    assert found == [
        {"name": "researcher-1", "installed": True, "running": ["t_1"], "queued": ["t_2"]},
        {"name": "researcher-2", "installed": True, "running": [], "queued": []},
    ]
    # Hosting names its workers in the dispatcher allowlist; one it names but has not
    # installed is reported as such.
    (tmp_path / "config.yaml").write_text("kanban:\n  dispatch_profiles: researcher-2,researcher-3\n")
    hosted = Context(profiles / "orchestrator", {})
    assert [(r["name"], r["installed"]) for r in researchers(hosted, cards)] == [
        ("researcher-2", True), ("researcher-3", False),
    ]

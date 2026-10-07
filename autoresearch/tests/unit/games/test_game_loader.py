import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoresearch.games.loader import Game, GameError, GameMetric, load_game
from autoresearch.io.docs import read_doc, write_doc

REPO_ROOT = Path(__file__).resolve().parents[3]
FAKE_TOOLS = REPO_ROOT / "games" / "fakegame" / "tools"

REQUIRED_FIELDS = [
    "name", "bindings", "reference_types", "outcome_metrics", "performance_guardrails",
    "min_episodes_floor", "league_id", "noise_source", "layers", "compiler",
]
BINDING_NAMES = ["build", "local_check", "upload", "evaluate", "replay_inspect"]


def _copy_fake(tmp_path, mutate):
    meta, body = read_doc(REPO_ROOT / "games" / "fakegame" / "GAME.md")
    mutate(meta)
    write_doc(tmp_path / "games" / "fakegame" / "GAME.md", meta, body)
    return tmp_path


def test_template_has_every_field():
    meta, body = read_doc(REPO_ROOT / "games" / "_template" / "GAME.md")
    for field in REQUIRED_FIELDS:
        assert field in meta, field
    assert set(meta["bindings"]) == set(BINDING_NAMES)
    for field in REQUIRED_FIELDS:
        assert field in body, f"body should explain {field}"


def test_fakegame_loads():
    game = load_game(REPO_ROOT, "fakegame")
    assert isinstance(game, Game)
    assert game.league_id == "fake-league"
    assert game.min_episodes_floor == 10
    assert game.compiler == "none"
    assert game.noise_source == "league_episodes"
    assert game.performance_guardrails == ["score"]
    assert game.outcome_metrics == [GameMetric("score", "up"), GameMetric("errors", "down")]
    assert set(game.bindings) == set(BINDING_NAMES)
    assert game.guide


@pytest.mark.parametrize("binding", BINDING_NAMES)
def test_missing_binding_names_it(tmp_path, binding):
    root = _copy_fake(tmp_path, lambda meta: meta["bindings"].pop(binding))
    with pytest.raises(GameError, match=binding):
        load_game(root, "fakegame")


def test_all_problems_are_named(tmp_path):
    def mutate(meta):
        del meta["league_id"]
        meta["compiler"] = "bogus"
        meta["performance_guardrails"] = ["nope"]

    with pytest.raises(GameError) as err:
        load_game(_copy_fake(tmp_path, mutate), "fakegame")
    for text in ("league_id", "compiler", "nope"):
        assert text in str(err.value)


def test_unknown_game(tmp_path):
    with pytest.raises(GameError, match="nosuch"):
        load_game(tmp_path, "nosuch")


def _run(tool, *args):
    out = subprocess.run(
        [sys.executable, str(FAKE_TOOLS / tool), *args, "--json"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def test_fake_build_deterministic(tmp_path):
    candidate = tmp_path / "cand"
    (candidate / "strategy").mkdir(parents=True)
    (candidate / "strategy" / "STRATEGY.md").write_text("aggression: 3\n")
    first = _run("fake_build.py", str(candidate), "--out", str(tmp_path / "b1"))
    second = _run("fake_build.py", str(candidate), "--out", str(tmp_path / "b2"))
    assert first["ok"] is True
    assert first["data"]["artifact_hash"] == second["data"]["artifact_hash"]
    assert (tmp_path / "b1" / "version.json").exists()
    (candidate / "strategy" / "STRATEGY.md").write_text("aggression: 4\n")
    third = _run("fake_build.py", str(candidate), "--out", str(tmp_path / "b3"))
    assert third["data"]["artifact_hash"] != first["data"]["artifact_hash"]


def test_fake_eval_deterministic_per_seed():
    a = _run("fake_eval.py", "--policy", "pol-a", "--seed", "1")
    assert a == _run("fake_eval.py", "--policy", "pol-a", "--seed", "1")
    assert a != _run("fake_eval.py", "--policy", "pol-a", "--seed", "2")
    metrics = a["data"]["metrics"]
    assert set(metrics) == {"score", "errors"}


def test_fake_eval_policy_shift_is_detectable():
    def mean_score(policy):
        scores = [_run("fake_eval.py", "--policy", policy, "--seed", str(s))
                  ["data"]["metrics"]["score"] for s in range(30)]
        return sum(scores) / len(scores)

    assert mean_score("pol-a") != mean_score("pol-b")


def test_seed_workspace_present():
    seed = REPO_ROOT / "games" / "fakegame" / "seed"
    assert (seed / "plan.md").exists()
    assert (seed / "strategy" / "STRATEGY.md").exists()

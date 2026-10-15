import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ide_research_core.games.loader import Game, GameError, GameMetric, load_game
from ide_research_core.io.docs import read_doc, write_doc

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


def test_starter_policy_section_is_required_and_present():
    """Every game ships a starter policy, the first champion when the league shows none for
    our player (A14); ``softmax bootstrap-champion`` follows its ``## Starter policy``."""
    _, template = read_doc(REPO_ROOT / "games" / "_template" / "GAME.md")
    _, fakegame = read_doc(REPO_ROOT / "games" / "fakegame" / "GAME.md")
    assert "`## Starter policy` section" in template
    assert "\n## Starter policy\n" in fakegame
    assert "Baseline build" not in template + fakegame


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


def test_fake_build_takes_a_candidate_directory_and_reads_parameters(tmp_path):
    candidate = tmp_path / "strategy" / "candidates" / "E-1"
    candidate.mkdir(parents=True)
    (candidate / "STRATEGY.md").write_text("# Strategy\n\n- aggression: 3\n- patience: 7.5\n")
    by_dir = _run("fake_build.py", str(candidate), "--out", str(tmp_path / "b1"))
    by_file = _run("fake_build.py", str(candidate / "STRATEGY.md"), "--out", str(tmp_path / "b2"))
    assert by_dir["data"]["parameters"] == {"aggression": 3, "patience": 7.5}
    assert by_dir["data"]["artifact_hash"] == by_file["data"]["artifact_hash"]
    policy = json.loads((tmp_path / "b1" / "policy.json").read_text())
    assert policy["parameters"] == {"aggression": 3, "patience": 7.5}


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


def _run_binding(game, binding, *args):
    command = shlex.split(game.bindings[binding])
    command = [sys.executable if part == "python3" else part for part in command]
    return subprocess.run(
        [*command, *args, "--json"], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )


def test_every_binding_is_runnable(tmp_path):
    game = load_game(REPO_ROOT, "fakegame")
    candidate = tmp_path / "cand"
    shutil.copytree(REPO_ROOT / "games" / "fakegame" / "seed", candidate)
    build_dir = tmp_path / "build"
    calls = {
        "build": [str(candidate), "--out", str(build_dir)],
        "local_check": [str(candidate)],
        "upload": [str(build_dir)],
        "evaluate": ["--policy", str(build_dir / "policy.json"), "--seed", "3"],
        "replay_inspect": ["--policy", str(build_dir / "policy.json"), "--seed", "3"],
    }
    assert set(calls) == set(BINDING_NAMES)
    for binding in BINDING_NAMES:  # build runs first so later bindings have its output
        result = _run_binding(game, binding, *calls[binding])
        assert result.returncode == 0, (binding, result.stderr, result.stdout)
        assert json.loads(result.stdout)["ok"] is True, binding


def test_upload_id_is_deterministic_and_version_path_relative(tmp_path):
    candidate = tmp_path / "cand"
    shutil.copytree(REPO_ROOT / "games" / "fakegame" / "seed", candidate)
    _run("fake_build.py", str(candidate), "--out", str(tmp_path / "b"))
    version = json.loads((tmp_path / "b" / "version.json").read_text())
    assert version["artifact"] == "policy.json"
    first = _run("fake_build.py", "--upload", str(tmp_path / "b"))
    assert first == _run("fake_build.py", "--upload", str(tmp_path / "b"))
    assert first["data"]["policy_version_id"].startswith("fake-pv-")


def test_bad_input_gives_error_envelope(tmp_path):
    bad = tmp_path / "policy.json"
    bad.write_text("not json")
    for tool, args in (
        ("fake_eval.py", ["--policy", str(bad), "--seed", "1"]),
        ("fake_build.py", ["--check", str(tmp_path)]),
        ("fake_build.py", ["--upload", str(tmp_path)]),
    ):
        result = subprocess.run(
            [sys.executable, str(FAKE_TOOLS / tool), *args, "--json"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 1, tool
        assert json.loads(result.stdout)["ok"] is False


def _game_with(tmp_path, extra: str):
    root = tmp_path / "repo"
    shutil.copytree(Path(__file__).resolve().parents[3] / "games" / "fakegame", root / "games" / "g")
    path = root / "games" / "g" / "GAME.md"
    text = path.read_text().replace("name: fakegame", "name: g", 1)
    path.write_text(text.replace("compiler: none\n", f"compiler: none\n{extra}", 1))
    return lambda: load_game(root, "g")


def test_source_is_optional_and_validated(tmp_path):
    assert load_game(Path(__file__).resolve().parents[3], "fakegame").source is None
    game = _game_with(tmp_path, "source: {repo: /srv/game.git, pin: coworld_manifest}\n")()
    assert game.source == {"repo": "/srv/game.git", "pin": "coworld_manifest"}
    bad = _game_with(tmp_path / "b", "source: {repo: '', pin: latest, extra: 1}\n")
    with pytest.raises(GameError) as error:
        bad()
    message = str(error.value)
    assert "source.repo" in message and "source.pin" in message and "source.extra" in message


def test_host_requirements_are_optional_and_validated(tmp_path):
    fakegame = load_game(Path(__file__).resolve().parents[3], "fakegame")
    assert (fakegame.host_env, fakegame.host_tools) == ((), ())
    game = _game_with(tmp_path, "host_requirements: {env: [GAME_PY], tools: [nim]}\n")()
    assert (game.host_env, game.host_tools) == (("GAME_PY",), ("nim",))
    bad = _game_with(tmp_path / "b", "host_requirements: {env: GAME_PY, tool: [nim]}\n")
    with pytest.raises(GameError) as error:
        bad()
    assert "host_requirements.env" in str(error.value)
    assert "host_requirements.tool" in str(error.value)


def test_gota_declares_its_build_host_requirements():
    """On the box, GotA builds failed fast on workers for want of these (GAME.md documents
    them as host requirements)."""
    gota = load_game(Path(__file__).resolve().parents[3], "gota")
    assert set(gota.host_env) == {"GOTA_HERMES_PYTHON"}
    assert gota.host_tools == ("nim",)

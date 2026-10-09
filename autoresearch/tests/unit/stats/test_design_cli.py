"""``autoresearch stats design``: freeze an experiment's evaluation design from the CLI."""

import json

import pytest
from records.fixtures import EXP_DESIGNED

from autoresearch.cli import main
from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore
from autoresearch.stats.noise import write_noise

VERSION = "fake-1"  # FakeSoftmax's default game version
REQUEST = {
    "baseline_version": "pv-base",
    "seats": [0],
    "opponent_count": 1,  # sampled from the (fake) league per seed (A12)
    "seeds": list(range(40)),
    # fakegame's performance guardrail is score, so the primary metric is errors.
    "primary_metrics": [{"name": "errors", "direction": "down", "min_effect": 1.0}],
    "guardrails": [],
}


def noise_record(version=VERSION):
    entry = {"mean": 50.0, "marginal_sd": 3.0, "paired_diff_sd": None, "n": 40, "n_pairs": 0}
    return {
        "game_version": version,
        "computed_at": "2026-10-07T00:00:00+00:00",
        "evidence": [],
        "metrics": {"score": entry, "errors": {**entry, "mean": 1.0, "marginal_sd": 1.0}},
    }


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(root))
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_SOFTMAX", "fake")
    for name in ("HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    return root


def new_experiment(root, status="designed", version=VERSION):
    meta = {k: v for k, v in EXP_DESIGNED.items() if k != "evaluation_design"}
    meta["environment"] = {**meta["environment"], "game_id": "fakegame", "game_version": version}
    store = RecordStore(Layout(root))
    exp = store.create("experiment", meta)
    return store, exp


def design(capsys, root, tmp_path, exp, *extra, request=REQUEST):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    code = main(["stats", "design", exp, "--request-file", str(path), "--root", str(root), *extra])
    return code, json.loads(capsys.readouterr().out)


def test_experiment_can_be_created_before_its_design_is_frozen(ws):
    store, exp = new_experiment(ws)
    assert "evaluation_design" not in store.load(exp)[0]


def test_leaving_designed_needs_a_frozen_design(ws):
    store, exp = new_experiment(ws)
    with pytest.raises(ValueError, match="evaluation_design"):
        store.set_status(exp, "built")


def test_design_freezes_from_noise_and_records_noise_rev(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    revision = write_noise(store.layout, noise_record())
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 0, env
    frozen = env["data"]["evaluation_design"]
    assert frozen["noise_rev"] == revision
    assert frozen["game_version"] == VERSION
    # n_min = max(floor 10, ceil((2.80159 * 1 * sqrt(2) / 1)^2)) = 16
    assert frozen["n_min"] == 16
    assert set(frozen["delta"]) == {"score"}
    assert {m["role"] for m in frozen["metrics"]} == {"primary", "performance"}
    meta, _ = store.load(exp)
    assert meta["evaluation_design"] == frozen
    assert meta["evaluation_design_request"] == REQUEST
    store.set_status(exp, "built")  # the frozen design satisfies the content rule
    # A12: one sampled line-up per seed, with the league snapshot it came from.
    assert frozen["opponents"] == [] and set(frozen["lineups"]) == {str(s) for s in range(40)}
    assert all(len(lineup) == 1 for lineup in frozen["lineups"].values())
    snapshot = frozen["league_snapshot"]
    assert snapshot["league_id"] == "fake-league" and snapshot["episodes_read"] == 100
    assert {e["policy"] for e in snapshot["entries"]} >= set(frozen["lineups"]["0"])


def test_a_fixed_opponent_set_needs_a_rationale(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    fixed = {k: v for k, v in REQUEST.items() if k != "opponent_count"}
    fixed["opponents"] = ["fake-house:v1"]
    code, env = design(capsys, ws, tmp_path, exp, request=fixed)
    assert code == 1 and "opponent_rationale" in env["error"]["message"]
    both = {**REQUEST, "opponents": ["fake-house:v1"]}
    code, env = design(capsys, ws, tmp_path, exp, request=both)
    assert code == 1 and "not both" in env["error"]["message"]
    reasoned = {**fixed, "opponent_rationale": "does this beat the house policy specifically"}
    code, env = design(capsys, ws, tmp_path, exp, request=reasoned)
    assert code == 0, env
    frozen = env["data"]["evaluation_design"]
    assert frozen["opponents"] == ["fake-house:v1"] and "lineups" not in frozen


def test_planned_pairs_changes_delta(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    _, by_seeds = design(capsys, ws, tmp_path, exp)
    _, by_flag = design(capsys, ws, tmp_path, exp, "--planned-pairs", "160")
    assert by_flag["data"]["planned_pairs"] == 160
    delta_seeds = by_seeds["data"]["evaluation_design"]["delta"]["score"]
    assert by_flag["data"]["evaluation_design"]["delta"]["score"] == pytest.approx(delta_seeds / 2)


def test_no_noise_for_the_current_version_needs_calibration(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 1 and env["error"]["reason"] == "needs_calibration"
    write_noise(store.layout, noise_record(version="old-version"))
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 1 and env["error"]["reason"] == "needs_calibration"
    assert "evaluation_design" not in store.load(exp)[0]


def test_environment_version_must_be_current(ws, tmp_path, capsys):
    store, exp = new_experiment(ws, version="fake-0")
    write_noise(store.layout, noise_record())
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 1 and env["error"]["reason"] == "version_mismatch"


def test_only_while_designed(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 0, env
    store.set_status(exp, "built")
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 1 and env["error"]["reason"] == "not_designed"


def test_bad_request_is_refused(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    request = {**REQUEST, "primary_metrics": []}
    code, env = design(capsys, ws, tmp_path, exp, request=request)
    assert code == 1 and env["error"]["reason"] == "bad_request"
    request = {k: v for k, v in REQUEST.items() if k != "guardrails"}
    code, env = design(capsys, ws, tmp_path, exp, request=request)
    assert code == 1 and "guardrails" in env["error"]["message"]
    request = {
        **REQUEST,
        "primary_metrics": [{"name": "score", "direction": "up", "min_effect": 1}],
    }
    code, env = design(capsys, ws, tmp_path, exp, request=request)
    assert code == 1 and "duplicate metric" in env["error"]["message"]


def test_too_few_seeds_is_refused(ws, tmp_path, capsys):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    code, env = design(capsys, ws, tmp_path, exp, request={**REQUEST, "seeds": [1, 2, 3]})
    assert code == 1 and env["error"]["reason"] == "bad_request"


def test_outside_a_profile_is_bad_arguments(ws, tmp_path, capsys, monkeypatch):
    store, exp = new_experiment(ws)
    write_noise(store.layout, noise_record())
    monkeypatch.delenv("HERMES_HOME")
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 2 and "profile" in env["error"]["message"]


def test_versions_compare_without_surrounding_whitespace(ws, tmp_path, capsys):
    store, exp = new_experiment(ws, version=f" {VERSION} ")
    write_noise(store.layout, noise_record(version=f"{VERSION}\n"))
    code, env = design(capsys, ws, tmp_path, exp)
    assert code == 0, env
    assert env["data"]["evaluation_design"]["game_version"] == VERSION

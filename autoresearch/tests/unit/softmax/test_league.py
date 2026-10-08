"""League reads: ``league_episodes`` / ``standings`` (fake and real client), the
``softmax_tool.league`` row mapping, and the ``softmax game-version | standings |
league-episodes`` commands feeding ``stats ingest --league`` and ``stats noise``."""

import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from autoresearch.cli import main
from autoresearch.softmax_tool.client import LEAGUE_FETCH_WORKERS, SoftmaxClient
from autoresearch.softmax_tool.fake import LEAGUE_HOUSE, LEAGUE_POLICIES, FakeSoftmax
from autoresearch.softmax_tool.league import episode_rows

from .conftest import LEAGUE_ID, completed, synthetic

ROUND = "round_7d1c0f6e-0000-4000-8000-000000000001"
EREQ = "ereq_0000aaaa-0000-4000-8000-000000000001"
COWORLD = "cow_3f0c2c1e-0000-4000-8000-000000000001"
GOLD = "div_aaaa0000-0000-4000-8000-000000000001"
SILVER = "div_aaaa0000-0000-4000-8000-000000000002"


# --- fake ---


def test_fake_league_episodes_are_deterministic_and_paired(tmp_path):
    sm = FakeSoftmax(tmp_path, {})
    episodes = sm.league_episodes("fake-league", 6)
    assert episodes == FakeSoftmax(tmp_path / "other", {}).league_episodes("fake-league", 6)
    assert [e["seed"] for e in episodes] == [1000, 1000, 1000, 1001, 1001, 1001]
    assert [e["seats"][0]["policy_version_id"] for e in episodes[:3]] == list(LEAGUE_POLICIES)
    assert {e["seats"][1]["policy_version_id"] for e in episodes} == {LEAGUE_HOUSE}
    assert all(e["game_version"] == "fake-1" for e in episodes)
    assert [entry["seat"] for entry in episodes[0]["results"]["episodes"]] == [0, 1]


def test_fake_standings_add_our_player_after_a_submission(tmp_path):
    sm = FakeSoftmax(tmp_path, {})
    assert [row["player_id"] for row in sm.standings("fake-league")] == [
        "ply_house_a",
        "ply_house_b",
    ]
    sm.submit("pv_x", "fake-league")
    rows = sm.standings("fake-league")
    assert [row["rank"] for row in rows] == [1, 2, 3]
    assert "ply_fake" in {row["player_id"] for row in rows}


# --- row mapping ---


def _episode(seats, entries_seed=7):
    return {
        "episode_id": "ereq_x",
        "seed": entries_seed,
        "game_version": None,
        "seats": seats,
        "results": {"game_version": "v9"},
    }


def _seat(position, policy):
    return {"position": position, "policy_version_id": policy, "player_id": None}


def test_episode_rows_map_seats_and_opponents(tmp_path):
    seen = []

    def evaluate(path):
        seen.append(json.loads(Path(path).read_text()))
        return [
            {"episode": 0, "seat": 0, "metrics": {"score": 1}},
            {"episode": 0, "seat": 1, "metrics": {"score": 2}, "status": "failed"},
            {"episode": 0, "seat": 2, "metrics": {"score": 3}},
        ]

    episode = _episode([_seat(0, "a:v1"), _seat(1, "b:v1"), _seat(2, None)])
    episode["results"]["game_version"] = " v9\n"  # versions compare stripped
    rows = episode_rows(episode, evaluate, tmp_path, "current")
    assert seen == [{"game_version": " v9\n"}]
    assert [(r.policy_version, r.seats, r.opponents) for r in rows] == [
        ("a:v1", (0,), ("b:v1", "human")),
        ("b:v1", (1,), ("a:v1", "human")),
    ]
    assert rows[0].episode_id == "ereq_x:0:s0" and rows[0].game_version == "v9"
    assert rows[1].status == "failed" and rows[0].seed == 7


def test_episode_rows_need_a_seat_when_several(tmp_path):
    episode = _episode([_seat(0, "a:v1"), _seat(1, "b:v1")])
    with pytest.raises(ValueError, match="no seat"):
        episode_rows(episode, lambda path: [{"metrics": {"score": 1}}], tmp_path, "c")
    single = _episode([_seat(0, "a:v1")])
    rows = episode_rows(single, lambda path: [{"metrics": {"score": 1}}], tmp_path, "c")
    assert rows[0].seats == (0,) and rows[0].opponents == ()


# --- real client (synthetic coworld 0.1.57 shapes; no live calls) ---


def league_runner(runner, results_response=None):
    return (
        runner.on("coworld", "rounds", response=completed(synthetic("coworld_rounds_league.json")))
        .on(
            "coworld",
            "episodes",
            "--round",
            response=completed(synthetic("coworld_episodes_round.json")),
        )
        .on(
            "coworld",
            "episodes",
            EREQ,
            response=completed(synthetic("coworld_episode_detail.json")),
        )
        .on(
            "coworld",
            "episode-results",
            response=results_response or completed(synthetic("coworld_episode_results.json")),
        )
        .on("coworld", "show", COWORLD, response=completed(json.dumps({"version": "65"})))
    )


def test_real_league_episodes_read_rounds_then_episodes(env, runner):
    sm = SoftmaxClient(env, run=league_runner(runner))
    episodes = list(sm.league_episodes(LEAGUE_ID, 10))
    assert len(episodes) == 1  # the failed episode request is skipped
    episode = episodes[0]
    # The seed is results.json's per-episode seed (the detail's seed is null, and
    # game_config.seed is a config value); the version is the detail's coworld_version.
    assert episode["episode_id"] == EREQ and episode["seed"] == 1450878938
    assert episode["game_version"] == "2026.10.7.2" and episode["round_id"] == ROUND
    assert [s["policy_version_id"] for s in episode["seats"]] == ["hero:v7", "rival:v4"]
    assert episode["results"]["scores"] == [61, 44]
    argvs = runner.argvs()
    assert not any(a[:2] == ["coworld", "show"] for a in argvs)
    assert [
        "coworld",
        "rounds",
        "--league",
        LEAGUE_ID,
        "--status",
        "completed",
        "--limit",
        "200",
        "--json",
    ] in argvs
    assert ["coworld", "episode-results", EREQ] in argvs
    assert not any(a[1] in ("submit", "upload-policy") for a in argvs if len(a) > 1)


def test_real_league_episodes_stop_at_limit(env, runner):
    sm = SoftmaxClient(env, run=league_runner(runner))
    assert len(list(sm.league_episodes(LEAGUE_ID, 1))) == 1


def many_episodes_runner(runner, count: int, delay: float = 0.02):
    """A round of ``count`` completed episodes whose detail calls are slow and counted."""
    listing = {
        "entries": [{"id": f"ereq_{i:04d}", "status": "completed"} for i in range(count)],
        "next_cursor": None,
    }
    detail = synthetic("coworld_episode_detail.json")
    state = {"in_flight": 0, "most": 0, "details": 0}
    lock = threading.Lock()

    def slow_detail(argv, kwargs):
        with lock:
            state["in_flight"] += 1
            state["details"] += 1
            state["most"] = max(state["most"], state["in_flight"])
        time.sleep(delay)
        with lock:
            state["in_flight"] -= 1
        return completed(detail)

    league_runner(runner).on(
        "coworld", "episodes", "--round", response=completed(json.dumps(listing))
    )
    for i in range(count):
        runner.on("coworld", "episodes", f"ereq_{i:04d}", response=slow_detail)
    return state


def test_real_league_episodes_fetch_concurrently_in_order(env, runner):
    state = many_episodes_runner(runner, 20)
    episodes = list(SoftmaxClient(env, run=runner).league_episodes(LEAGUE_ID, 20))
    assert [e["episode_id"] for e in episodes] == [f"ereq_{i:04d}" for i in range(20)]
    assert 1 < state["most"] <= LEAGUE_FETCH_WORKERS


def test_real_league_episodes_stop_early_with_little_in_flight(env, runner):
    state = many_episodes_runner(runner, 40)
    stream = SoftmaxClient(env, run=runner).league_episodes(LEAGUE_ID, 40)
    next(stream)
    stream.close()  # the consumer failed (for example an evaluate error)
    assert state["details"] <= 2 * LEAGUE_FETCH_WORKERS + 1 + LEAGUE_FETCH_WORKERS


def test_real_version_falls_back_to_coworld_show(env, runner):
    detail = json.loads(synthetic("coworld_episode_detail.json"))
    del detail["coworld_version"]
    league_runner(runner).on("coworld", "episodes", EREQ, response=completed(json.dumps(detail)))
    [episode] = list(SoftmaxClient(env, run=runner).league_episodes(LEAGUE_ID, 5))
    assert episode["game_version"] == "65"


def test_real_standings_walk_the_division_ladder(env, runner):
    runner.on(
        "coworld",
        "results",
        LEAGUE_ID,
        response=completed(synthetic("coworld_results_league.json")),
    )
    runner.on(
        "coworld",
        "results",
        GOLD,
        response=completed(synthetic("coworld_results_division_gold.json")),
    )
    runner.on(
        "coworld",
        "results",
        SILVER,
        response=completed(synthetic("coworld_results_division_silver.json")),
    )
    rows = SoftmaxClient(env, run=runner).standings(LEAGUE_ID)
    assert [(r["division_name"], r["rank"], r["player_id"]) for r in rows] == [
        ("Gold", 1, "ply_hero"),
        ("Gold", 2, "ply_rival"),
    ]


# --- CLI (fake platform, fakegame binding) ---


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_SOFTMAX", "fake")
    for name in ("HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def run(capsys, *argv):
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_league_episodes_reports_progress_on_stderr(profile, capsys):
    out = profile / "rows.jsonl"
    assert main(["softmax", "league-episodes", "--limit", "25", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["data"]["episodes"] == 25  # stdout stays one envelope
    lines = captured.err.splitlines()
    assert [line.split(" (")[0] for line in lines] == [
        "league-episodes: 10/25 episodes, 20 rows",
        "league-episodes: 20/25 episodes, 40 rows",
        "league-episodes: done: 25 episodes, 50 rows",
    ]


def test_game_version_and_standings_commands(profile, capsys):
    code, env = run(capsys, "softmax", "game-version")
    assert code == 0 and env["data"] == {"league_id": "fake-league", "game_version": "fake-1"}
    code, env = run(capsys, "softmax", "standings")
    assert code == 0 and env["data"]["rows"][0]["player_id"] == "ply_house_a"


def test_league_episodes_feed_ingest_and_noise(profile, capsys):
    out = profile / "ws" / ".scratch" / "league.jsonl"
    code, env = run(
        capsys,
        "softmax",
        "league-episodes",
        "--league",
        "fake-league",
        "--limit",
        "60",
        "--out",
        str(out),
    )
    assert code == 0, env
    assert env["data"]["episodes"] == 60 and env["data"]["rows"] == 120
    assert env["data"]["game_versions"] == ["fake-1"]
    root = str(profile / "ws")
    code, env = run(
        capsys,
        "stats",
        "ingest",
        "--rows",
        str(out),
        "--league",
        "fake-league",
        "--game-version",
        "fake-1",
        "--game",
        "fakegame",
        "--root",
        root,
    )
    assert code == 0 and env["data"]["report"]["kept"] == 120
    code, env = run(
        capsys,
        "stats",
        "noise",
        "--league",
        "fake-league",
        "--game-version",
        "fake-1",
        "--metrics",
        "score,errors",
        "--root",
        root,
    )
    assert code == 0, env


    record = yaml.safe_load((profile / "ws" / "stats" / "metric-noise.yaml").read_text())
    assert record["metrics"]["score"]["n_pairs"] >= 10  # three policies per seed vs the house


def test_league_episodes_rejects_bad_limit(profile, capsys, tmp_path):
    code, _ = run(
        capsys, "softmax", "league-episodes", "--limit", "0", "--out", str(tmp_path / "r")
    )
    assert code == 2

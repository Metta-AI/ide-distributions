"""Read-only system flow against a real local Observatory and its player session.

Set COGAMES_API_URL, SOFTMAX_PLAYER_CREDENTIAL, RESEARCH_TEST_PLAYER and
RESEARCH_TEST_LEAGUES (two comma-separated league IDs) after seeding the local platform.
The credential may belong to a different player than the player being researched.
"""

import json
import os
import subprocess
import sys
from urllib.parse import urlparse

import pytest


@pytest.mark.skipif(not os.environ.get("RESEARCH_TEST_LEAGUES"), reason="needs real local platform")
def test_discover_and_switch_games_without_a_workspace_binding(tmp_path):
    assert urlparse(os.environ["COGAMES_API_URL"]).hostname in {"localhost", "127.0.0.1"}
    leagues = os.environ["RESEARCH_TEST_LEAGUES"].split(",")
    assert len(set(leagues)) == 2, "Seed two different games to exercise conversation switching"
    player = os.environ["RESEARCH_TEST_PLAYER"]
    env = {key: value for key, value in os.environ.items() if not key.startswith("RESEARCH_")}
    env.update(HERMES_HOME=str(tmp_path), RESEARCH_GAME="no-game-selected")

    def call(*args):
        result = subprocess.run(
            [sys.executable, "-m", "ide_research_core", "softmax", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        response = json.loads(result.stdout)
        assert response["ok"], response
        return response["data"]

    games = []
    for league_id in [*leagues, leagues[0]]:
        found = call("discover-leagues", "--query", league_id)
        assert [league["id"] for league in found["leagues"]] == [league_id]
        support = call("game-support", "--league", league_id)
        assert support["league"]["id"] == league_id
        assert support["coworld"]["id"]
        assert "manifest" not in support["coworld"]
        guide = next(doc for doc in support["documents"] if doc["id"] == "participation")
        page = call("game-support", "--league", league_id, "--document", guide["id"],
                    "--expected-sha256", guide["sha256"], "--limit", "100")
        assert 0 < len(page["document"]["content"]) <= 100
        games.append(support["league"]["game"]["id"])
        ranked = call("league-players", "--league", league_id, "--limit", "1")
        assert ranked["league_id"] == league_id and len(ranked["players"]) <= 1
        for action in ("player-champion", "player-result"):
            observation = call(action, "--league", league_id, "--player", player)
            assert observation["league_id"] == league_id
            assert observation["player_id"] == player
    assert games[0] != games[1] and games[0] == games[2]
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "workspace").exists(), (
        "Discovery must not bootstrap or retarget research"
    )

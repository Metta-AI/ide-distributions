"""Run the installed game tools without writing into distribution-owned source."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("PUDGE_NATIVE_TESTS") != "1",
    reason="Requires Nim and real pinned game/dependency downloads",
)
def test_readonly_game_tools_run_and_reuse_profile_cache(tmp_path):
    profile = tmp_path / "profile"
    source = Path(__file__).resolve().parents[2]
    game = profile / "games/pudge-wars-basic"
    shutil.copytree(
        source / "games/pudge-wars-basic",
        game,
        ignore=shutil.ignore_patterns(".cache", "bin", "__pycache__"),
    )
    paths = [game, *game.rglob("*")]
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    env = {
        **os.environ,
        "HERMES_HOME": str(profile),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PUDGE_LIVE_ENGINE": str(source / "tests/fixtures/pudge_wars_basic/live_engine.json"),
    }
    try:
        episode = tmp_path / "task-one/episodes"
        match = subprocess.run(
            [
                sys.executable,
                str(game / "tools/match.py"),
                "--seat",
                "base:3",
                "--seat",
                "aggressive:3",
                "--seed",
                "7",
                "--ticks",
                "3000",
                "--out",
                str(episode),
                "--json",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=2400,
            check=False,
        )
        assert match.returncode == 0, match.stdout + match.stderr
        result = json.loads(match.stdout)
        assert result["ok"]
        assert result["data"]["episodes"][0]["state_hash"] == "00000000FADD3447"
        assert result["data"]["episodes"][0]["vm_failed_slots"] == []
        replay = subprocess.run(
            [
                sys.executable,
                str(game / "tools/replay_inspect.py"),
                str(episode / "seed-7"),
                "--json",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert replay.returncode == 0, replay.stdout + replay.stderr
        assert json.loads(replay.stdout)["ok"]
        shutil.rmtree(episode.parent)
        for target in ("pudge_headless", "pudge_league"):
            reused = subprocess.run(
                [
                    sys.executable,
                    str(game / "tools/native.py"),
                    "build",
                    "--target",
                    target,
                    "--json",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            assert reused.returncode == 0, reused.stdout + reused.stderr
            receipt = json.loads(reused.stdout)["data"]
            assert receipt["reused"]
            assert Path(receipt["binary"]).is_relative_to(profile / "cache")
    finally:
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)

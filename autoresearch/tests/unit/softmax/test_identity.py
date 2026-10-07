import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from autoresearch.softmax_tool.identity import (
    IdentityError,
    identity_problems,
    require_player,
    whoami,
)
from autoresearch.softmax_tool.platform_cli import SERVER, PlatformCli
from autoresearch.softmax_tool.protocol import Identity

from .conftest import PLAYER_ID, TOKEN, completed, credential_value, recorded, synthetic


def make_cli(env, runner) -> PlatformCli:
    return PlatformCli(env, run=runner)


def test_whoami_parses_recorded_status_and_takes_expiry_from_credential(env, runner):
    expires_at = datetime(2026, 10, 8, 21, 45, 29, tzinfo=UTC)
    env["SOFTMAX_PLAYER_CREDENTIAL"] = credential_value(expires_at)
    identity = whoami(make_cli(env, runner))
    assert (identity.subject_type, identity.player_id) == ("player", PLAYER_ID)
    assert identity.expires_at == expires_at
    assert runner.argvs() == [["softmax", "status"]]


def test_credentials_file_is_profile_local_player_session_with_mode_0600(env, runner):
    whoami(make_cli(env, runner))
    home = Path(env["HERMES_HOME"]) / "local" / "softmax" / "home"
    path = home / ".softmax" / "credentials.yaml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = yaml.safe_load(path.read_text())
    # No user token: an expired player session cannot fall back to acting as a user.
    assert data["tokens"] == {}
    sessions = data["player_sessions"][SERVER]
    assert sessions["cache"][sessions["active"]]["token"] == TOKEN

    _, kwargs = runner.calls[0]
    assert kwargs["env"]["HOME"] == str(home)
    assert kwargs["cwd"] == str(home)
    assert "SOFTMAX_PLAYER_CREDENTIAL" not in kwargs["env"]
    assert all(TOKEN not in value for value in kwargs["env"].values())


def test_executables_come_from_the_running_venv(env):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv[0])
        return completed(recorded("softmax_status.txt"))

    import sys

    whoami(PlatformCli(env, run=run))
    assert seen == [str(Path(sys.executable).parent / "softmax")]


def test_no_identity_cache_every_call_runs_status(env, runner):
    cli = make_cli(env, runner)
    whoami(cli)
    whoami(cli)
    assert runner.argvs() == [["softmax", "status"], ["softmax", "status"]]


def test_status_failure_is_identity_error(env, runner):
    runner.on("softmax", "status", response=completed(stdout="Not authenticated.", returncode=1))
    with pytest.raises(IdentityError, match="Not authenticated"):
        whoami(make_cli(env, runner))


@pytest.mark.parametrize("value", ["", TOKEN, json.dumps({"token": TOKEN})])
def test_malformed_credential_is_rejected_without_echoing_it(env, runner, value):
    env["SOFTMAX_PLAYER_CREDENTIAL"] = value
    with pytest.raises(IdentityError) as raised:
        whoami(make_cli(env, runner))
    assert TOKEN not in str(raised.value)
    assert runner.calls == []


def test_require_player_accepts_matching_player(env, runner):
    identity = require_player(make_cli(env, runner), PLAYER_ID, datetime.now(UTC))
    assert identity.player_id == PLAYER_ID


def test_require_player_rejects_user_subject(env, runner):
    runner.on("softmax", "status", response=completed(synthetic("softmax_status_user.txt")))
    with pytest.raises(IdentityError, match="user"):
        require_player(make_cli(env, runner), PLAYER_ID, datetime.now(UTC))


def test_require_player_rejects_other_player_naming_both(env, runner):
    with pytest.raises(IdentityError) as raised:
        require_player(make_cli(env, runner), "ply_other", datetime.now(UTC))
    assert PLAYER_ID in str(raised.value) and "ply_other" in str(raised.value)


def test_require_player_rejects_expired_credential(env, runner):
    env["SOFTMAX_PLAYER_CREDENTIAL"] = credential_value(datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(IdentityError, match="expired"):
        require_player(make_cli(env, runner), PLAYER_ID, datetime.now(UTC))


def test_require_player_rejects_missing_expected_id(env, runner):
    with pytest.raises(IdentityError, match="AUTORESEARCH_PLAYER_ID"):
        require_player(make_cli(env, runner), "", datetime.now(UTC))


def test_identity_problems_is_the_single_predicate():
    now = datetime.now(UTC)
    good = Identity("player", PLAYER_ID, now + timedelta(hours=1))
    assert identity_problems(good, PLAYER_ID, now) == []
    bad = Identity("user", "usr_1", now - timedelta(seconds=1))
    problems = identity_problems(bad, PLAYER_ID, now)
    assert len(problems) == 3
    assert any("'user'" in p for p in problems) and any("expired" in p for p in problems)
    assert identity_problems(good, "", now) == ["AUTORESEARCH_PLAYER_ID is not set"]

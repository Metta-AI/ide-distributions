import json
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from ide_research_core.softmax_tool.identity import (
    CredentialRejected,
    IdentityError,
    identity_problems,
    require_player,
    whoami,
)
from ide_research_core.softmax_tool.platform_cli import DEFAULT_SERVER, PlatformCli, SoftmaxCliError
from ide_research_core.softmax_tool.protocol import Identity

from .conftest import (
    AGENT_TOKEN,
    PLAYER_ID,
    TOKEN,
    FakeRunner,
    ServerOptionMissing,
    completed,
    credential_value,
    recorded,
    synthetic,
)


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
    sessions = data["player_sessions"][DEFAULT_SERVER]
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


# --- cga_ agent tokens (the Softmax hosting runtime's player credential) ---


def test_agent_token_is_a_non_expiring_player_credential(env, runner):
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    identity = require_player(make_cli(env, runner), PLAYER_ID, datetime.now(UTC))
    assert (identity.subject_type, identity.player_id) == ("player", PLAYER_ID)
    assert identity.expires_at is None
    assert runner.argvs() == [["softmax", "status"]]


def test_agent_token_is_written_as_the_server_token_like_the_hosting_runtime(env, runner):
    """metta observatory_hosted_worker/runtime.py writes ``{"tokens": {<server>: cga_...}}``;
    the profile-local file has the same shape, and no player session."""
    env["SOFTMAX_PLAYER_CREDENTIAL"] = f"  {AGENT_TOKEN}\n"
    cli = make_cli(env, runner)
    whoami(cli)
    assert stat.S_IMODE(cli.credentials_path.stat().st_mode) == 0o600
    data = yaml.safe_load(cli.credentials_path.read_text())
    assert data == {"tokens": {DEFAULT_SERVER: AGENT_TOKEN}, "player_sessions": {}}
    _, kwargs = runner.calls[0]
    assert all(AGENT_TOKEN not in value for value in kwargs["env"].values())


def test_switching_credential_kind_rewrites_the_whole_file(env, runner):
    cli = make_cli(env, runner)
    whoami(cli)
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    whoami(make_cli(env, runner))
    data = yaml.safe_load(cli.credentials_path.read_text())
    assert data["player_sessions"] == {}  # the old player session is gone


def test_agent_token_never_expires_in_identity_problems():
    now = datetime.now(UTC)
    assert identity_problems(Identity("player", PLAYER_ID, None), PLAYER_ID, now) == []


def test_agent_token_for_another_player_is_refused(env, runner):
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    with pytest.raises(IdentityError, match="ply_other"):
        require_player(make_cli(env, runner), "ply_other", datetime.now(UTC))


@pytest.mark.parametrize(
    "value", [json.dumps({"token": AGENT_TOKEN}), "cga_", "ply_bare_token", "cga_has space"]
)
def test_other_shapes_are_still_rejected_without_echoing(env, runner, value):
    env["SOFTMAX_PLAYER_CREDENTIAL"] = value
    with pytest.raises(IdentityError) as raised:
        whoami(make_cli(env, runner))
    assert AGENT_TOKEN not in str(raised.value) and "ply_bare_token" not in str(raised.value)
    assert runner.calls == []


@pytest.mark.parametrize(
    "output",
    [
        synthetic("softmax_status_rejected.txt"),
        synthetic("softmax_status_401.stderr"),  # softmax-cli's rendering of a 401
        "Error: Request failed (401) for /whoami: Failed to authenticate",  # coworld's
        "Active player session expired (ply_x). Run softmax player use to refresh.",
        "Not authenticated. Run softmax login first.",
    ],
)
def test_rejected_credential_is_a_plain_credential_rejected(env, runner, output):
    """A revoked agent token (or a disabled player) reads as anonymous on /whoami, which
    ``softmax status`` reports as an invalid token; other routes answer 401. The message keeps
    the platform's words but not the CLI's advice (``softmax login`` is not our recovery)."""
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    runner.on("softmax", "status", response=completed(stderr=output, returncode=1))
    with pytest.raises(CredentialRejected) as raised:
        whoami(make_cli(env, runner))
    message = str(raised.value)
    assert "Softmax did not accept the player credential" in message
    assert "SOFTMAX_PLAYER_CREDENTIAL" in message and DEFAULT_SERVER in message
    assert "softmax login" not in message and "Run " not in message and "Next:" not in message
    assert AGENT_TOKEN not in message


@pytest.mark.parametrize(
    "output",
    [
        synthetic("softmax_status_503.stderr"),
        recorded("softmax_status_connect_error.stderr"),
        "Error: Request failed (502) for /whoami: Bad Gateway",
    ],
)
def test_unreachable_platform_is_not_a_rejection(env, runner, output):
    """A network error or a 5xx says nothing about the credential: a plain ``IdentityError``
    (mutating calls still fail closed), never ``CredentialRejected``."""
    runner.on("softmax", "status", response=completed(stderr=output, returncode=1))
    with pytest.raises(IdentityError) as raised:
        whoami(make_cli(env, runner))
    assert not isinstance(raised.value, CredentialRejected)
    assert "whoami failed" in str(raised.value)
    assert "did not accept" not in str(raised.value)


def test_a_traceback_is_never_a_rejection_even_when_its_source_lines_match(env, runner):
    """softmax-cli prints an uncaught error (here: connection refused, captured offline) as a
    Rich traceback with source lines, and ``status_cmd``'s frame quotes its own "Not
    authenticated." message. A shallow traceback puts that frame inside the text we keep, so
    a traceback must never count as a rejection (review N1)."""
    lines = recorded("softmax_status_connect_error.stderr").splitlines()
    frame = next(i for i, line in enumerate(lines) if "Not authenticated" in line)
    shallow = "\n".join([lines[0], *lines[frame - 3 : frame + 3], lines[-1]])
    assert "Not authenticated" in shallow
    runner.on("softmax", "status", response=completed(stderr=shallow, returncode=1))
    with pytest.raises(IdentityError) as raised:
        whoami(make_cli(env, runner))
    assert not isinstance(raised.value, CredentialRejected)
    assert "did not accept" not in str(raised.value)


def hang(argv, **kwargs):
    raise subprocess.TimeoutExpired(argv, kwargs["timeout"])


def test_timeout_is_a_cli_error_naming_the_command_not_the_token(env):
    """A hung CLI is a ``SoftmaxCliError`` (worker commands print the normal ``platform``
    envelope), and the message names the command, never the token."""
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    with pytest.raises(SoftmaxCliError) as raised:
        PlatformCli(env, run=hang).run("coworld", ["xp-request", "get", "xreq_1"], timeout=5)
    message = str(raised.value)
    assert message == "coworld xp-request get xreq_1 timed out after 5 s"
    assert AGENT_TOKEN not in message


def test_timeout_in_whoami_is_not_a_rejection(env):
    with pytest.raises(IdentityError) as raised:
        whoami(PlatformCli(env, run=hang))
    assert not isinstance(raised.value, CredentialRejected)
    assert "whoami failed: softmax status timed out" in str(raised.value)


# --- server selection ---


def test_server_comes_from_cogames_api_url(env):
    """``COGAMES_API_URL`` is what the hosting runtime writes and what softmax-cli reads; the
    credential is keyed by it and every call passes it as ``--server``."""
    env["COGAMES_API_URL"] = "http://localhost:8000/api/"
    runner = FakeRunner(server="http://localhost:8000/api")
    runner.on("softmax", "status", response=completed(recorded("softmax_status.txt")))
    for value in (credential_value(), AGENT_TOKEN):
        env["SOFTMAX_PLAYER_CREDENTIAL"] = value
        cli = make_cli(env, runner)
        assert cli.server == "http://localhost:8000/api"
        whoami(cli)
        data = yaml.safe_load(cli.credentials_path.read_text())
        assert set(data["tokens"]) | set(data["player_sessions"]) == {"http://localhost:8000/api"}
    _, kwargs = runner.calls[0]
    assert "COGAMES_API_URL" not in kwargs["env"]  # the explicit --server is the only source


def test_server_defaults_to_softmax_com(env):
    assert make_cli(env, FakeRunner()).server == "https://softmax.com/api" == DEFAULT_SERVER


# --- the fake runner's --server check ---


def test_missing_server_option_escapes_except_exception():
    runner = FakeRunner().on("softmax", "status", response=completed())
    swallowed = []
    with pytest.raises(ServerOptionMissing):
        try:
            runner(["/venv/bin/softmax", "status"])
        except Exception as error:  # noqa: BLE001 - what code under test does
            swallowed.append(error)
    assert swallowed == []
    assert runner(["/venv/bin/softmax", "status", "--server", DEFAULT_SERVER]).returncode == 0

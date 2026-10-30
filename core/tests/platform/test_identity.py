import json
from datetime import UTC, datetime, timedelta

import pytest
from softmax_client import credentials as softmax_credentials

from research_core.platform import identity
from research_core.platform.errors import IdentityUnavailable, PlatformError

SERVER = "https://platform.test/api"


def in_hours(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def test_server_default_and_override():
    assert identity.server({}) == "https://softmax.com/api"
    assert identity.server({"COGAMES_API_URL": "http://localhost:8000/"}) == "http://localhost:8000"


def test_player_token_bare():
    env = {"SOFTMAX_PLAYER_CREDENTIAL": " cga_abc \n"}
    assert identity.player_token(env) == "cga_abc"
    assert identity.player_credential(env).expires_at is None


def test_player_token_json_session():
    expires = in_hours(12)
    env = {"SOFTMAX_PLAYER_CREDENTIAL": json.dumps({"token": "ply_tok", "expires_at": expires})}
    credential = identity.player_credential(env)
    assert credential.token == "ply_tok" and credential.expires_at == expires
    assert identity.player_token(env) == "ply_tok"


def test_expired_player_session_is_refused():
    env = {
        "SOFTMAX_PLAYER_CREDENTIAL": json.dumps({"token": "ply_old", "expires_at": in_hours(-1)})
    }
    with pytest.raises(PlatformError) as info:
        identity.player_token(env)
    assert not info.value.retryable
    assert "Player session expired" in str(info.value)
    assert "SOFTMAX_PLAYER_CREDENTIAL" in str(info.value)


def test_player_credential_problems():
    with pytest.raises(IdentityUnavailable) as info:
        identity.player_token({})
    assert info.value.identity == "player"
    with pytest.raises(PlatformError, match="not valid JSON"):
        identity.player_token({"SOFTMAX_PLAYER_CREDENTIAL": "{not json"})
    with pytest.raises(PlatformError, match='"token"'):
        identity.player_token(
            {"SOFTMAX_PLAYER_CREDENTIAL": json.dumps({"expires_at": in_hours(1)})}
        )
    with pytest.raises(PlatformError, match="not a timestamp"):
        identity.player_token(
            {"SOFTMAX_PLAYER_CREDENTIAL": json.dumps({"token": "t", "expires_at": "soon"})}
        )


def test_player_id():
    assert identity.player_id({"SOFTMAX_PLAYER_ID": "ply_1"}) == "ply_1"
    assert identity.player_id({"SOFTMAX_PLAYER_ID": ""}) is None
    assert identity.player_id({}) is None


def test_user_token_from_env_wins(tmp_path):
    env = {
        "SOFTMAX_USER_CREDENTIAL": "user_env",
        "SOFTMAX_CONFIG_DIR": str(tmp_path),
        "COGAMES_API_URL": SERVER,
    }
    assert identity.user_token(env) == "user_env"
    assert identity.token_for(env, "user") == "user_env"


def test_user_token_from_softmax_login_file(tmp_path, monkeypatch):
    # The softmax CLI writes the file; its loader reads SOFTMAX_CONFIG_DIR from os.environ.
    monkeypatch.setenv("SOFTMAX_CONFIG_DIR", str(tmp_path))
    softmax_credentials.save_user_token(server=SERVER, token="user_saved")
    env = {"SOFTMAX_CONFIG_DIR": str(tmp_path), "COGAMES_API_URL": SERVER + "/"}
    assert identity.user_token(env) == "user_saved"
    assert identity.user_token({**env, "COGAMES_API_URL": "https://other.test"}) is None


def test_user_token_absent(tmp_path):
    env = {"SOFTMAX_CONFIG_DIR": str(tmp_path / "missing"), "COGAMES_API_URL": SERVER}
    assert identity.user_token(env) is None
    with pytest.raises(IdentityUnavailable) as info:
        identity.token_for(env, "user")
    assert info.value.identity == "user" and not info.value.retryable
    assert str(info.value) == "User identity not provisioned for this workspace"


def test_saved_player_token_is_not_a_user_identity(tmp_path):
    # Hosts put the Player token into tokens[server] so the CLIs act as the Player; that file
    # must not make the Player pass as the User.
    env = {
        "SOFTMAX_CONFIG_DIR": str(tmp_path),
        "COGAMES_API_URL": SERVER,
        "SOFTMAX_PLAYER_CREDENTIAL": "cga_player",
    }
    identity.write_credentials_dir(env, tmp_path)
    assert identity.user_token(env) is None
    assert identity.token_for(env, "player") == "cga_player"


def test_write_credentials_dir_is_what_the_cli_reads(tmp_path, monkeypatch):
    dest = tmp_path / "softmax"
    env = {"COGAMES_API_URL": SERVER, "SOFTMAX_PLAYER_CREDENTIAL": "cga_player"}
    path = identity.write_credentials_dir(env, dest)
    assert path == dest / "credentials.yaml"
    assert (dest.stat().st_mode & 0o777) == 0o700
    assert (path.stat().st_mode & 0o777) == 0o600

    monkeypatch.setenv("SOFTMAX_CONFIG_DIR", str(dest))
    assert softmax_credentials.load_current_token(server=SERVER) == "cga_player"
    assert softmax_credentials.load_player_session(server=SERVER) is None


def test_credentials_file_default_is_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert identity.credentials_file({}) == tmp_path / ".softmax" / "credentials.yaml"
    assert identity.credentials_file({"SOFTMAX_CONFIG_DIR": "/x"}).parent.as_posix() == "/x"

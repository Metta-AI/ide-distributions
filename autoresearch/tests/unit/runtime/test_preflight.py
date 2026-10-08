import json
from collections import namedtuple
from datetime import UTC, datetime

import yaml

from autoresearch.runtime.preflight import preflight
from autoresearch.softmax_tool.fake import FakeSoftmax

NOW = datetime(2026, 10, 7, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")
GB = 1024**3


def run(tmp_path, faults="", disk=10 * GB, player="ply_a"):
    env = {"AUTORESEARCH_PLAYER_ID": player, "AUTORESEARCH_FAKE_FAULTS": faults}
    sm = FakeSoftmax(tmp_path, env, clock=lambda: NOW)
    return preflight(env, sm, tmp_path, NOW, disk_free=lambda _p: Usage(0, 0, disk))


def test_ok(tmp_path):
    result = run(tmp_path)
    assert result.ok and not result.reasons and result.credits == 100


def test_user_token_fails(tmp_path):
    result = run(tmp_path, "identity=user")
    assert not result.ok and "not a player" in result.reasons[0]


def test_wrong_player_names_both(tmp_path):
    result = run(tmp_path, "player=ply_b")
    assert not result.ok and any("ply_a" in r and "ply_b" in r for r in result.reasons)


def test_expired_fails(tmp_path):
    result = run(tmp_path, "expires_in=-60")
    assert not result.ok and "expired" in result.reasons[0]


def test_expiring_soon_warns(tmp_path):
    result = run(tmp_path, "expires_in=3600")
    assert result.ok and result.warnings


def test_low_disk_fails(tmp_path):
    result = run(tmp_path, disk=GB // 2)
    assert not result.ok and "MB free" in result.reasons[0]


def test_zero_credits_warns(tmp_path):
    result = run(tmp_path, "credits=0")
    assert result.ok and result.credits == 0 and result.warnings


def test_whoami_exception(tmp_path):
    result = run(tmp_path, "fail_after=whoami")
    assert not result.ok and "injected fault" in result.reasons[0]


def test_missing_player_id_fails(tmp_path):
    result = run(tmp_path, player="")
    assert not result.ok and any("AUTORESEARCH_PLAYER_ID" in r for r in result.reasons)


def test_disk_check_oserror_fails(tmp_path):
    env = {"AUTORESEARCH_PLAYER_ID": "ply_a"}
    sm = FakeSoftmax(tmp_path, env, clock=lambda: NOW)

    def missing(_path):
        raise FileNotFoundError("no such dir")

    result = preflight(env, sm, tmp_path, NOW, disk_free=missing)
    assert not result.ok and "cannot check disk" in result.reasons[0]


# --- A3 host dispatcher config, A5 OpenRouter credit ---


def host_run(tmp_path, root_config: dict | None, profile="autoresearch", **kwargs):
    home = tmp_path / "root" / "profiles" / profile
    home.mkdir(parents=True)
    if root_config is not None:
        (tmp_path / "root" / "config.yaml").write_text(yaml.safe_dump(root_config))
    env = {"AUTORESEARCH_PLAYER_ID": "ply_a", **kwargs.pop("env", {})}
    sm = FakeSoftmax(tmp_path, env, clock=lambda: NOW)
    return preflight(
        env, sm, tmp_path, NOW, disk_free=lambda _p: Usage(0, 0, 10 * GB), hermes_home=home,
        **kwargs,
    )  # fmt: skip


def test_host_config_defaults_pass(tmp_path):
    assert host_run(tmp_path, None).ok
    assert host_run(tmp_path / "b", {"kanban": {"dispatch_profiles": ["autoresearch"]}}).ok


def test_host_default_assignee_fails(tmp_path):
    result = host_run(tmp_path, {"kanban": {"default_assignee": "default"}})
    assert not result.ok and any("default_assignee" in r for r in result.reasons)


def test_host_dispatch_profiles_without_us_fails(tmp_path):
    result = host_run(tmp_path, {"kanban": {"dispatch_profiles": ["other"]}})
    assert not result.ok and any("dispatch_profiles" in r for r in result.reasons)


def test_host_dispatch_in_gateway_off_fails(tmp_path):
    result = host_run(tmp_path, {"kanban": {"dispatch_in_gateway": False}})
    assert not result.ok and any("dispatch_in_gateway" in r for r in result.reasons)


def test_host_config_skipped_outside_profiles(tmp_path):
    env = {"AUTORESEARCH_PLAYER_ID": "ply_a"}
    sm = FakeSoftmax(tmp_path, env, clock=lambda: NOW)
    home = tmp_path / "plainhome"
    home.mkdir()
    result = preflight(
        env, sm, tmp_path, NOW, disk_free=lambda _p: Usage(0, 0, 10 * GB), hermes_home=home
    )
    assert result.ok and any("host config" in w for w in result.warnings)


class FakeHttp:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.error:
            raise self.error
        return json.dumps(self.payload)


def test_openrouter_low_credit_pauses(tmp_path):
    http = FakeHttp({"data": {"limit_remaining": 4.5}})
    result = host_run(tmp_path, None, env={"OPENROUTER_API_KEY": "sk-x"}, http_get=http)
    assert not result.ok and any("OpenRouter" in r for r in result.reasons)
    url, headers = http.calls[0]
    assert url == "https://openrouter.ai/api/v1/key"
    assert headers["Authorization"] == "Bearer sk-x"


def test_openrouter_enough_or_unlimited_credit_passes(tmp_path):
    for index, remaining in enumerate((25.0, None)):
        http = FakeHttp({"data": {"limit_remaining": remaining}})
        result = host_run(
            tmp_path / str(index), None, env={"OPENROUTER_API_KEY": "sk-x"}, http_get=http
        )
        assert result.ok


def test_openrouter_key_read_from_the_profile_env_file(tmp_path):
    # Under the gateway a cron script never receives provider keys (hermes-facts item 7).
    home = tmp_path / "root" / "profiles" / "autoresearch"
    home.mkdir(parents=True)
    (home / ".env").write_text("OTHER=1\nOPENROUTER_API_KEY=sk-file\n")
    http = FakeHttp({"data": {"limit_remaining": 1.0}})
    env = {"AUTORESEARCH_PLAYER_ID": "ply_a"}
    sm = FakeSoftmax(tmp_path, env, clock=lambda: NOW)
    result = preflight(
        env, sm, tmp_path, NOW, disk_free=lambda _p: Usage(0, 0, 10 * GB),
        hermes_home=home, http_get=http,
    )  # fmt: skip
    assert not result.ok and http.calls[0][1]["Authorization"] == "Bearer sk-file"


def test_openrouter_check_skipped_without_key_or_in_test_mode(tmp_path):
    http = FakeHttp(error=AssertionError("must not be called"))
    no_key = host_run(tmp_path / "a", None, http_get=http)
    skipped = host_run(
        tmp_path / "b",
        None,
        env={"OPENROUTER_API_KEY": "sk-x", "AUTORESEARCH_SKIP_OPENROUTER_CHECK": "1"},
        http_get=http,
    )
    assert no_key.ok and skipped.ok and http.calls == []
    assert any("OpenRouter" in w for w in no_key.warnings + skipped.warnings)


def test_openrouter_request_failure_is_a_warning(tmp_path):
    http = FakeHttp(error=OSError("network down"))
    result = host_run(tmp_path, None, env={"OPENROUTER_API_KEY": "sk-x"}, http_get=http)
    assert result.ok and any("network down" in w for w in result.warnings)
    assert not any("sk-x" in w for w in result.warnings)

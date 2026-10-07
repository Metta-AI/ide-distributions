from collections import namedtuple
from datetime import UTC, datetime

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

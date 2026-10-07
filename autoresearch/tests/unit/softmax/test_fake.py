import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from autoresearch.softmax_tool.fake import FakeSoftmax
from autoresearch.softmax_tool.protocol import CreditDenied, XpHandle, from_env

SPEC = {"episodes": 3}


def make(tmp_path, **env):
    return FakeSoftmax(tmp_path, env)


def test_state_persists_across_processes(tmp_path):
    code = (
        "import sys; from pathlib import Path; from autoresearch.softmax_tool.fake import FakeSoftmax;"
        "sm = FakeSoftmax(Path(sys.argv[1]), {}); pv = sm.upload(Path('a.bin'), 'a');"
        "sm.submit(pv, 'lg_1'); print(pv)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, check=True
    )
    pv = out.stdout.strip()
    sm = make(tmp_path)
    assert [s["policy_version_id"] for s in sm.submissions("ply_fake")] == [pv]
    assert sm.leaderboard_champion("ply_fake", "lg_1") == pv


def test_default_identity_and_env_player(tmp_path):
    ident = make(tmp_path, AUTORESEARCH_PLAYER_ID="ply_x").whoami()
    assert (ident.subject_type, ident.player_id) == ("player", "ply_x")
    assert ident.expires_at > datetime.now(UTC) + timedelta(hours=23)
    assert make(tmp_path / "b").whoami().player_id == "ply_fake"


def test_identity_faults(tmp_path):
    sm = make(tmp_path, AUTORESEARCH_FAKE_FAULTS="identity=user;player=ply_other;expires_in=60")
    ident = sm.whoami()
    assert (ident.subject_type, ident.player_id) == ("user", "ply_other")
    assert ident.expires_at < datetime.now(UTC) + timedelta(seconds=120)


def test_credits_fault_denies_xp(tmp_path):
    sm = make(tmp_path, AUTORESEARCH_FAKE_FAULTS="credits=0")
    assert sm.credits() == 0
    assert isinstance(sm.xp_create("E-1", 1, 1, SPEC), CreditDenied)


def test_xp_lifecycle_and_recover(tmp_path):
    sm = make(tmp_path)
    handle = sm.xp_create("E-1", 1, 1, SPEC)
    assert isinstance(handle, XpHandle) and handle.experiment == "E-1"
    assert sm.xp_recover("E-1") == [handle]
    assert sm.xp_recover("E-2") == []
    status = sm.xp_status(handle)
    assert status.state == "completed" and status.episodes_completed == status.episodes_total == 3
    files = sm.xp_artifacts(handle, tmp_path / "out")
    assert files and all(p.exists() for p in files)
    text = files[0].read_text()
    assert "score" in text and "errors" in text
    again = make(tmp_path / "other").xp_create("E-1", 1, 1, SPEC)
    assert again == handle  # deterministic ids


def test_fail_after_persists_then_raises(tmp_path):
    sm = make(tmp_path, AUTORESEARCH_FAKE_FAULTS="fail_after=submit")
    pv = sm.upload(tmp_path / "a", "a")
    with pytest.raises(RuntimeError, match="injected fault after submit"):
        sm.submit(pv, "lg_1")
    assert len(make(tmp_path).submissions("ply_fake")) == 1


def test_champion_is_latest_and_none_when_empty(tmp_path):
    sm = make(tmp_path)
    assert sm.leaderboard_champion("ply_fake", "lg_1") is None
    first, second = sm.upload(tmp_path / "a", "a"), sm.upload(tmp_path / "b", "b")
    sm.submit(first, "lg_1")
    sm.submit(second, "lg_1")
    assert sm.leaderboard_champion("ply_fake", "lg_1") == second
    assert sm.leaderboard_champion("ply_fake", "lg_2") is None


def test_game_version_default(tmp_path):
    assert make(tmp_path).game_version("lg_1") == "fake-1"


def test_from_env(tmp_path):
    assert isinstance(from_env({"AUTORESEARCH_SOFTMAX": "fake"}, tmp_path), FakeSoftmax)
    with pytest.raises(NotImplementedError):
        from_env({}, tmp_path)


def test_xp_create_idempotent_single_charge(tmp_path):
    sm = make(tmp_path)
    before = sm.credits()
    first = sm.xp_create("E-1", 1, 1, SPEC)
    assert sm.xp_create("E-1", 1, 1, SPEC) == first
    assert sm.credits() == before - 1
    assert sm.xp_recover("E-1") == [first]


def test_xp_create_retry_after_fail_after_not_double_charged(tmp_path):
    crashing = make(tmp_path, AUTORESEARCH_FAKE_FAULTS="fail_after=xp_create")
    before = crashing.credits()
    with pytest.raises(RuntimeError):
        crashing.xp_create("E-1", 1, 1, SPEC)
    sm = make(tmp_path)
    handle = sm.xp_create("E-1", 1, 1, SPEC)
    assert sm.xp_recover("E-1") == [handle]
    assert sm.credits() == before - 1


def test_xp_create_retry_at_zero_credits_returns_existing(tmp_path):
    sm = make(tmp_path)
    handle = sm.xp_create("E-1", 1, 1, SPEC)
    state_path = tmp_path / ".supervisor/runtime/fake_softmax.json"
    state = json.loads(state_path.read_text())
    state["credits"] = 0
    state_path.write_text(json.dumps(state))
    assert sm.xp_create("E-1", 1, 1, SPEC) == handle
    assert isinstance(sm.xp_create("E-1", 2, 1, SPEC), CreditDenied)

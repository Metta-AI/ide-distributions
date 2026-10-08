import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from autoresearch.softmax_tool.fake import FakeSoftmax
from autoresearch.softmax_tool.identity import IdentityError
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


def test_whoami_answers_for_the_credential_not_the_expected_player(tmp_path):
    """A fake credential that names its player (``player_id``) is who whoami reports, so a
    wrong ``AUTORESEARCH_PLAYER_ID`` is caught (C6) as it is on the real platform."""
    credential = json.dumps({"token": "t", "expires_at": "2099-01-01T00:00:00Z",
                             "player_id": "ply_a"})  # fmt: skip
    sm = make(tmp_path, AUTORESEARCH_PLAYER_ID="ply_wrong", SOFTMAX_PLAYER_CREDENTIAL=credential)
    assert sm.whoami().player_id == "ply_a"
    with pytest.raises(IdentityError, match="ply_wrong"):
        sm.submit("pv_1", "lg_1")
    plain = make(tmp_path / "b", AUTORESEARCH_PLAYER_ID="ply_x", SOFTMAX_PLAYER_CREDENTIAL="fake")
    assert plain.whoami().player_id == "ply_x"  # no player in the credential: the expected one


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


def seeded_spec(seed, policy):
    return {
        "num_episodes": 1,
        "roster": [{"slot": 0, "player": {"policy_ref": policy}}],
        "game_config_overrides": {"seed": seed},
    }


def test_fake_honors_seed_override_and_tags_seed_and_arm(tmp_path):
    sm = make(tmp_path)
    first = sm.xp_create("E-1", 1, 1, seeded_spec(7, "pv:a"), seed=7, arm="candidate")
    same = sm.xp_create("E-1", 1, 2, seeded_spec(7, "pv:a"), seed=7, arm="candidate")
    other_arm = sm.xp_create("E-1", 1, 1, seeded_spec(7, "pv:b"), seed=7, arm="baseline")
    other_seed = sm.xp_create("E-1", 1, 1, seeded_spec(8, "pv:a"), seed=8, arm="candidate")
    assert first.seed == 7 and first.arm == "candidate"
    assert (
        len({first.request_id, same.request_id, other_arm.request_id, other_seed.request_id}) == 4
    )
    assert set(sm.xp_recover("E-1")) == {first, same, other_arm, other_seed}

    def results(handle):
        (path,) = sm.xp_artifacts(handle, tmp_path / handle.request_id)
        return json.loads(path.read_text())

    assert results(first)["seed"] == 7
    assert results(first)["episodes"][0]["seed"] == 7
    # same seed and policy -> the same episode, whatever the request
    assert results(first)["episodes"] == results(same)["episodes"]
    assert results(first)["episodes"] != results(other_seed)["episodes"]


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

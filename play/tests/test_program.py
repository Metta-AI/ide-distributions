import hashlib
import json
from pathlib import Path

import pytest
from conftest import CREDIT_STDERR, LEAGUE_STDERR, PERMIT_STDERR, PLAYER, STARTER, STARTER_SHA

from play.__main__ import run
from play.games import GOTA
from play.program import PlayError


def test_starter_fetch_checks_the_published_sha256(program, cli, fetched, env):
    data = program.starter_fetch()
    target = Path(env["PLAY_WORKSPACE"]) / "starter/baseline.bas"
    assert data == {
        "coworld_id": "cow_gota",
        "player": "baseline",
        "name": "Baseline",
        "sha256": STARTER_SHA,
        "size_bytes": len(STARTER),
        "path": str(target),
    }
    assert target.read_bytes() == STARTER
    assert fetched == [
        f"https://example.invalid/api/observatory/v2/coworlds/cow_gota/player-files/{STARTER_SHA}"
    ]
    assert program.starter_fetch() == data  # safe to repeat
    assert cli.mutations() == []


def test_starter_fetch_refuses_bytes_that_do_not_match(program, env):
    with pytest.raises(PlayError) as error:
        program.starter_fetch("other")
    assert error.value.reason == "sha256_mismatch"
    assert not (Path(env["PLAY_WORKSPACE"]) / "starter/other.bas").exists()
    with pytest.raises(PlayError) as error:
        program.starter_fetch("missing")
    assert error.value.reason == "starter_not_found"
    assert error.value.details["available"] == ["baseline", "other"]


def test_upload_checks_the_player_and_scopes_the_name(program, cli, env):
    (Path(env["PLAY_WORKSPACE"]) / "program.bas").write_bytes(b"10 END\n")
    data = program.program_upload("program.bas", "my-first")
    assert data["policy_version"] == f"my-first-{PLAYER}:v3"
    assert data["sha256"] == hashlib.sha256(b"10 END\n").hexdigest()
    upload = cli.calls.index(
        ["coworld", "upload-policy", "--file", data["path"], "--name", f"my-first-{PLAYER}"]
    )
    assert cli.calls[upload - 1] == ["softmax", "status"]
    # No research bookkeeping: nothing but the program file in the workspace.
    assert sorted(p.name for p in Path(env["PLAY_WORKSPACE"]).rglob("*")) == ["program.bas"]


def test_upload_refuses_another_player_and_files_outside_the_workspace(program, cli, env):
    (Path(env["PLAY_WORKSPACE"]) / "program.bas").write_bytes(b"10 END\n")
    outside = Path(env["HERMES_HOME"]) / "x.bas"
    outside.write_bytes(b"10 END\n")
    for path, name in ((str(outside), "a"), ("program.bas", "Bad Name"), ("missing.bas", "a")):
        with pytest.raises(PlayError) as error:
            program.program_upload(path, name)
        assert error.value.reason == "bad_arguments"
    cli.player = "ply_someone_else"
    envelope = run("program_upload", {"path": "program.bas", "name": "a"}, program)
    assert envelope["ok"] is False and envelope["error"]["reason"] == "identity"
    assert cli.mutations() == []


def test_practice_request_is_private_and_idempotent(program, cli):
    data = program.practice_request(f"p-{PLAYER}:v3", episodes=2, seats=[0, 5])
    assert data == {
        "policy_version": f"p-{PLAYER}:v3",
        "league_id": GOTA.league_id,
        "outcome": "requested",
        "request_id": "xreq_1",
        "episodes": 2,
        "seats": [0, 5],
    }
    body = json.loads(
        cli.inputs[cli.calls.index(["coworld", "xp-request", "create", "-", "--json"])]
    )
    assert body == {
        "target": {"league_id": GOTA.league_id},
        "roster": [
            {"slot": seat, "player": {"policy_ref": f"p-{PLAYER}:v3"}}
            if seat in (0, 5)
            else {"slot": seat, "player": {"random": True}}
            for seat in range(10)
        ],
        "num_episodes": 2,
        "private": True,
        "title": f"practice:p-{PLAYER}:v3:a1",
        "idempotency_key": f"practice:p-{PLAYER}:v3:a1",
    }
    assert cli.calls[-2] == ["softmax", "status"]


@pytest.mark.parametrize(
    "stderr, expected",
    [
        (
            PERMIT_STDERR,
            {
                "refusal": "permit_required",
                "state": "waiting",
                "ask": "ask_1",
                "reason": "Your coach asks first.",
            },
        ),
        (CREDIT_STDERR, {"refusal": "credit_denied", "reason": "no credits left"}),
        (LEAGUE_STDERR, {"refusal": "league_not_chosen"}),
    ],
)
def test_practice_refusals_are_typed_and_never_retried(program, cli, stderr, expected):
    cli.create = ("refuse", stderr)
    data = program.practice_request(f"p-{PLAYER}:v3")
    assert data["outcome"] == "refused"
    assert {k: data[k] for k in expected} == expected
    assert len(cli.mutations()) == 1


def test_practice_status_downloads_results_once_completed(program, cli, env):
    cli.xp_status = "running"
    data = program.practice_status("xreq_1")
    assert data["state"] == "running" and "results" not in data and "episodes" not in data
    cli.xp_status = "completed"
    data = program.practice_status("xreq_1")
    assert data["state"] == "completed"
    assert data["episodes_completed"] == data["episodes_total"] == 2
    results = Path(env["PLAY_WORKSPACE"]) / "practice/xreq_1/abc/results.json"
    assert data["results"] == [str(results)]
    assert data["episodes"] == [
        {"episode_id": "ep-1", "watch_url": "https://example.invalid/observatory/episodes/ep-1"}
    ]
    assert cli.mutations() == []


def test_practice_status_treats_not_produced_as_terminal(program, cli, env):
    cli.xp_status = "completed"
    cli.replay_state = "not_produced"
    data = program.practice_status("xreq_1")
    assert "results_pending" not in data and len(data["results"]) == 1
    cli.replay_state = "pending"
    assert "results_pending" in program.practice_status("xreq_1")


def test_release_submits_once(program, cli):
    version = f"p-{PLAYER}:v3"
    data = program.program_release(version)
    assert data == {
        "policy_version": version,
        "league_id": GOTA.league_id,
        "outcome": "released",
        "submission_id": "sub_1",
        "already": False,
    }
    assert [
        "coworld",
        "submit",
        version,
        "--league",
        GOTA.league_id,
        "--player",
        PLAYER,
        "--no-open-browser",
    ] in cli.calls
    cli.submissions = [
        {
            "id": "sub_1",
            "policy_version": {"label": version},
            "league": {"id": GOTA.league_id},
            "status": "pending",
        }
    ]
    cli.calls.clear()
    data = program.program_release(version)
    assert data["outcome"] == "released" and data["already"] is True
    assert cli.mutations() == []


@pytest.mark.parametrize(
    "stderr, refusal",
    [
        (PERMIT_STDERR, "permit_required"),
        (LEAGUE_STDERR, "league_not_chosen"),
        (CREDIT_STDERR, "credit_denied"),
    ],
)
def test_release_refusals_are_typed(program, cli, stderr, refusal):
    cli.submit = ("refuse", stderr)
    data = program.program_release(f"p-{PLAYER}:v3")
    assert data["outcome"] == "refused" and data["refusal"] == refusal
    assert len(cli.mutations()) == 1
    if refusal == "credit_denied":
        assert data["reason"] == "no credits left"


def test_bad_arguments_never_reach_the_platform(program, cli):
    for operation, arguments in [
        ("practice_request", {"policy_version": "no-version"}),
        ("practice_request", {"policy_version": "p:v1", "seats": [10]}),
        ("practice_request", {"policy_version": "p:v1", "episodes": 21}),
        ("practice_status", {"request_id": "../etc"}),
        ("program_release", {"policy_version": "p"}),
        ("program_release", {"policy_version": "p:v1", "league": "other"}),
        ("promotion_submit", {}),
    ]:
        envelope = run(operation, arguments, program)
        assert envelope["ok"] is False and envelope["error"]["reason"] == "bad_arguments"
    assert cli.calls == []

import json
from pathlib import Path

import pytest

from autoresearch.softmax_tool.client import CreditsUnavailable, SoftmaxClient
from autoresearch.softmax_tool.fake import FakeSoftmax
from autoresearch.softmax_tool.identity import IdentityError
from autoresearch.softmax_tool.protocol import from_env

from .conftest import (
    AGENT_TOKEN,
    FIXTURES,
    LEAGUE_ID,
    PLAYER_ID,
    FakeRunner,
    completed,
    recorded,
    synthetic,
)

LABEL = "ar-E-01K6Z3V8QXAAAAAAAAAAAAAAAA:v1"
SPEC = {"league_id": LEAGUE_ID, "num_episodes": 4, "roster": [{"player": {"random": True}}] * 10}
MUTATING = (
    ("coworld", "upload-policy"),
    ("coworld", "submit"),
    ("coworld", "xp-request", "create"),
)


def make(env, runner, tmp_path) -> SoftmaxClient:
    return SoftmaxClient(env, run=runner)


def saw_mutation(runner) -> bool:
    return any(tuple(argv[: len(m)]) == m for argv in runner.argvs() for m in MUTATING)


def with_mutations(runner):
    return (
        runner.on("coworld", "upload-policy", response=completed(synthetic("upload_policy.txt")))
        .on("coworld", "submit", response=completed(synthetic("submit.txt")))
        .on(
            "coworld",
            "xp-request",
            "list",
            response=completed(recorded("xp_request_list_mine.json")),
        )
        .on(
            "coworld",
            "xp-request",
            "create",
            response=completed(synthetic("xp_request_create.json")),
        )
    )


def test_from_env_selects_fake_or_real(env, tmp_path):
    assert isinstance(from_env({**env, "AUTORESEARCH_SOFTMAX": "fake"}, tmp_path), FakeSoftmax)
    assert isinstance(from_env(env, tmp_path), SoftmaxClient)


@pytest.mark.parametrize(
    "status_fixture, expected_player",
    [
        ("synthetic/softmax_status_user.txt", PLAYER_ID),
        ("recorded/softmax_status.txt", "ply_other"),
    ],
)
@pytest.mark.parametrize(
    "action",
    [
        lambda sm, tmp: sm.upload(tmp / "policy.bas", "ar-E-x"),
        lambda sm, tmp: sm.submit(LABEL, LEAGUE_ID),
        lambda sm, tmp: sm.xp_create("E-01K6Z3V8QXAAAAAAAAAAAAAAAA", 1, 1, SPEC),
    ],
    ids=["upload", "submit", "xp_create"],
)
def test_wrong_identity_refuses_before_any_mutating_call(
    env, runner, tmp_path, status_fixture, expected_player, action
):
    runner.on("softmax", "status", response=completed((FIXTURES / status_fixture).read_text()))
    with_mutations(runner)
    env["AUTORESEARCH_PLAYER_ID"] = expected_player
    with pytest.raises(IdentityError):
        action(make(env, runner, tmp_path), tmp_path)
    assert not saw_mutation(runner)
    assert ["softmax", "status"] in runner.argvs()


def test_upload_checks_identity_immediately_before_and_returns_label(env, runner, tmp_path):
    with_mutations(runner)
    artifact = tmp_path / "policy.bas"
    artifact.write_text("10 PRINT 1\n")
    label = make(env, runner, tmp_path).upload(artifact, "ar-E-01K6Z3V8QXAAAAAAAAAAAAAAAA")
    assert label == LABEL
    assert runner.argvs() == [
        ["softmax", "status"],
        [
            "coworld",
            "upload-policy",
            "--file",
            str(artifact.resolve()),
            "--name",
            "ar-E-01K6Z3V8QXAAAAAAAAAAAAAAAA",
        ],
    ]
    _, kwargs = runner.calls[1]
    assert kwargs["env"]["COWORLD_AGENT_GUIDANCE"] == "0"


def test_upload_without_completion_line_fails(env, runner, tmp_path):
    runner.on("coworld", "upload-policy", response=completed("Policy name: x\n"))
    with pytest.raises(RuntimeError, match="Upload complete"):
        make(env, runner, tmp_path).upload(tmp_path / "p.bas", "x")


def test_submit_names_the_player_and_returns_submission_id(env, runner, tmp_path):
    with_mutations(runner)
    submission_id = make(env, runner, tmp_path).submit(LABEL, LEAGUE_ID)
    assert submission_id == "sub_00000000-0000-4000-8000-000000000051"
    assert runner.argvs() == [
        ["softmax", "status"],
        [
            "coworld",
            "submit",
            LABEL,
            "--league",
            LEAGUE_ID,
            "--player",
            PLAYER_ID,
            "--no-open-browser",
        ],
    ]


def test_every_mutation_rechecks_identity(env, runner, tmp_path):
    with_mutations(runner)
    sm = make(env, runner, tmp_path)
    sm.submit(LABEL, LEAGUE_ID)
    sm.submit(LABEL, LEAGUE_ID)
    assert [argv[0:2] for argv in runner.argvs()] == [
        ["softmax", "status"],
        ["coworld", "submit"],
        ["softmax", "status"],
        ["coworld", "submit"],
    ]


def test_submissions_reads_recorded_listing(env, runner, tmp_path):
    runner.on("coworld", "submissions", response=completed(recorded("submissions_league.json")))
    rows = make(env, runner, tmp_path).submissions(PLAYER_ID)
    assert len(rows) == 6
    assert {row["player"]["id"] for row in rows} == {PLAYER_ID}
    assert runner.argvs() == [["coworld", "submissions", "--player", PLAYER_ID, "--json"]]


def test_leaderboard_champion_none_from_recorded_empty_listing(env, runner, tmp_path):
    runner.on(
        "coworld", "memberships", response=completed(recorded("memberships_champions_player.json"))
    )
    assert make(env, runner, tmp_path).leaderboard_champion(PLAYER_ID, LEAGUE_ID) is None
    assert runner.argvs() == [
        [
            "coworld",
            "memberships",
            "--league",
            LEAGUE_ID,
            "--player",
            PLAYER_ID,
            "--champions-only",
            "--json",
        ]
    ]


def test_leaderboard_champion_returns_policy_label(env, runner, tmp_path):
    runner.on(
        "coworld", "memberships", response=completed(synthetic("memberships_champions_player.json"))
    )
    champion = make(env, runner, tmp_path).leaderboard_champion(PLAYER_ID, LEAGUE_ID)
    assert champion == "games-bond-gota:v5"


def test_game_version_resolves_league_coworld(env, runner, tmp_path):
    runner.on("coworld", "leagues", response=completed(recorded("leagues_get.json")))
    runner.on("coworld", "show", response=completed(recorded("coworld_show.json")))
    assert make(env, runner, tmp_path).game_version(LEAGUE_ID) == "2026.10.6.1"
    assert runner.argvs() == [
        ["coworld", "leagues", LEAGUE_ID, "--json"],
        ["coworld", "show", "cow_162aa34f-f447-4c5d-8db3-480ec83e98f4", "--json"],
    ]


def test_credits_are_unavailable_to_a_player_credential(env, runner, tmp_path):
    with pytest.raises(CreditsUnavailable):
        make(env, runner, tmp_path).credits()
    assert runner.calls == []


def test_cli_failure_message_carries_stderr(env, runner, tmp_path):
    runner.on("coworld", "submissions", response=completed(stderr="Error: boom", returncode=1))
    with pytest.raises(RuntimeError, match="boom"):
        make(env, runner, tmp_path).submissions(PLAYER_ID)


def test_read_only_calls_do_not_need_identity_check(env, runner, tmp_path):
    runner.on("coworld", "submissions", response=completed(recorded("submissions_league.json")))
    make(env, runner, tmp_path).submissions(PLAYER_ID)
    assert ["softmax", "status"] not in runner.argvs()
    assert Path(env["HERMES_HOME"], "local", "softmax", "home", ".softmax").is_dir()


@pytest.mark.parametrize("credential", ["", "ply_bare_token", '{"token": "ply_x"}'])
def test_bad_credential_is_identity_error_before_any_mutating_call(
    env, runner, tmp_path, credential
):
    with_mutations(runner)
    env["SOFTMAX_PLAYER_CREDENTIAL"] = credential
    sm = make(env, runner, tmp_path)
    for action in (
        lambda: sm.upload(tmp_path / "policy.bas", "ar-E-x"),
        lambda: sm.submit(LABEL, LEAGUE_ID),
        lambda: sm.xp_create("E-01K6Z3V8QXAAAAAAAAAAAAAAAA", 1, 1, SPEC),
    ):
        with pytest.raises(IdentityError):
            action()
    assert not saw_mutation(runner)


def test_game_source_pin_reads_the_manifest_source_url(env, runner, tmp_path):
    runner.on("coworld", "leagues", response=completed(recorded("leagues_get.json")))
    runner.on(
        "coworld", "show", response=completed(synthetic("coworld_show_with_manifest.json"))
    )
    pin = make(env, runner, tmp_path).game_source_pin(LEAGUE_ID)
    assert pin == "0123456789abcdef0123456789abcdef01234567"


def test_game_source_pin_is_none_without_a_pinned_source_url(env, runner, tmp_path):
    runner.on("coworld", "leagues", response=completed(recorded("leagues_get.json")))
    runner.on("coworld", "show", response=completed(recorded("coworld_show.json")))
    assert make(env, runner, tmp_path).game_source_pin(LEAGUE_ID) is None


def test_game_version_is_stripped(env, runner, tmp_path):
    shown = json.loads(recorded("coworld_show.json"))
    runner.on("coworld", "leagues", response=completed(recorded("leagues_get.json")))
    runner.on("coworld", "show", response=completed(json.dumps({**shown, "version": " 7.1\n"})))
    assert make(env, runner, tmp_path).game_version(LEAGUE_ID) == "7.1"


def test_agent_token_on_a_local_server_runs_the_whole_cycle(env, tmp_path):
    """A ``cga_`` agent token with ``COGAMES_API_URL`` set (the hosting runtime's setup): every
    mutating call still checks whoami first, and every call (``FakeRunner`` asserts it) passes
    the configured server as ``--server``."""
    env["SOFTMAX_PLAYER_CREDENTIAL"] = AGENT_TOKEN
    env["COGAMES_API_URL"] = "http://127.0.0.1:8000/api"
    runner = FakeRunner(server="http://127.0.0.1:8000/api")
    runner.on("softmax", "status", response=completed(recorded("softmax_status.txt")))
    with_mutations(runner).on(
        "coworld", "submissions", response=completed(recorded("submissions_league.json"))
    )
    sm = make(env, runner, tmp_path)
    assert sm.whoami().expires_at is None
    sm.upload(tmp_path / "policy.bas", "ar-E-01K6Z3V8QXAAAAAAAAAAAAAAAA")
    sm.submit(LABEL, LEAGUE_ID)
    sm.xp_create("E-01K6Z3V8QXAAAAAAAAAAAAAAAA", 9, 1, SPEC)
    sm.submissions(PLAYER_ID)
    tools = [argv[:2] for argv in runner.argvs()]
    assert tools == [
        ["softmax", "status"],
        ["softmax", "status"],
        ["coworld", "upload-policy"],
        ["softmax", "status"],
        ["coworld", "submit"],
        ["coworld", "xp-request"],
        ["softmax", "status"],
        ["coworld", "xp-request"],
        ["coworld", "submissions"],
    ]

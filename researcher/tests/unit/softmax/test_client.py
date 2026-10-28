import json
import urllib.error
from pathlib import Path

import pytest

from ide_research_core.softmax_tool.client import CREDITS_PATH, SoftmaxClient
from ide_research_core.softmax_tool.fake import FakeSoftmax
from ide_research_core.softmax_tool.identity import IdentityError
from ide_research_core.softmax_tool.platform_cli import DEFAULT_SERVER, SERVER_ENV, SoftmaxCliError
from ide_research_core.softmax_tool.protocol import LeagueNotChosen, PermitRequired, from_env

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
    assert isinstance(from_env({**env, "RESEARCH_SOFTMAX": "fake"}, tmp_path), FakeSoftmax)
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
    env["RESEARCH_PLAYER_ID"] = expected_player
    with pytest.raises(IdentityError):
        action(make(env, runner, tmp_path), tmp_path)
    assert not saw_mutation(runner)
    assert ["softmax", "status"] in runner.argvs()


@pytest.mark.parametrize("artifact_type, flag", [("file", "--file"), ("oci-image", "--image-archive")])
def test_upload_checks_identity_immediately_before_and_returns_label(env, runner, tmp_path, artifact_type, flag):
    with_mutations(runner)
    artifact = tmp_path / "policy.bas"
    artifact.write_text("10 PRINT 1\n")
    label = make(env, runner, tmp_path).upload(artifact, "ar-E-01K6Z3V8QXAAAAAAAAAAAAAAAA", artifact_type=artifact_type)
    assert label == LABEL
    assert runner.argvs() == [
        ["softmax", "status"],
        [
            "coworld",
            "upload-policy",
            flag,
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


def _submit_fails_with(runner, fixture: str):
    return runner.on(
        "coworld", "submit", response=completed(stderr=synthetic(fixture), returncode=1)
    )


def test_submit_permit_refusal_returns_permit_required(env, runner, tmp_path):
    """The coach's IDE Controls ask before league submissions (403 permit_required)."""
    _submit_fails_with(runner, "submit_403_permit.stderr")
    result = make(env, runner, tmp_path).submit(LABEL, LEAGUE_ID)
    assert isinstance(result, PermitRequired)
    assert result.ask == "permit-submit-dc43b411e9401d8b-1"
    assert result.reason.startswith(f"submit on league:{LEAGUE_ID}:")


def test_submit_league_choice_refusal_returns_league_not_chosen(env, runner, tmp_path):
    _submit_fails_with(runner, "submit_403_league_elsewhere.stderr")
    result = make(env, runner, tmp_path).submit(LABEL, LEAGUE_ID)
    assert isinstance(result, LeagueNotChosen)
    assert result.reason.startswith("This cogent plays league_0b1c2d3e")
    assert result.reason.endswith(f"a submission to {LEAGUE_ID} is refused.")


def test_submit_other_403_still_raises(env, runner, tmp_path):
    _submit_fails_with(runner, "submit_403_other.stderr")
    with pytest.raises(SoftmaxCliError, match="does not belong to you"):
        make(env, runner, tmp_path).submit(LABEL, LEAGUE_ID)


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


CREDITS_BODY = {
    "status": {"balance_credits": 11872.6, "max_balance_credits": 20000.0, "enforced": True},
    "recent_requests": [],
}


def test_credits_reads_the_balance_as_the_player(env, runner):
    """A10: ``GET /observatory/usage/me/credits`` with the Player token (no CLI command in
    coworld 0.1.58), with a User-Agent because Cloudflare blocks urllib's default one."""
    seen = []

    def http_get(url, headers):
        seen.append((url, headers))
        return json.dumps(CREDITS_BODY)

    assert SoftmaxClient(env, run=runner, http_get=http_get).credits() == 11872.6
    [(url, headers)] = seen
    assert url == f"{DEFAULT_SERVER}{CREDITS_PATH}"
    assert headers["Authorization"] == f"Bearer {json.loads(env['SOFTMAX_PLAYER_CREDENTIAL'])['token']}"
    assert headers["User-Agent"]
    assert runner.calls == []


def test_episode_cost_is_the_recent_mean(env, runner):
    body = {
        "status": CREDITS_BODY["status"],
        "recent_requests": [
            {"episodes": 2, "compute_cost_credits": 3.0},
            {"episodes": 4, "compute_cost_credits": 9.0},
        ],
    }
    client = SoftmaxClient(env, run=runner, http_get=lambda url, headers: json.dumps(body))
    assert client.episode_cost() == 2.0
    empty = SoftmaxClient(env, run=runner, http_get=lambda url, headers: json.dumps(CREDITS_BODY))
    assert empty.episode_cost() is None


def test_credits_use_the_configured_server(env, runner):
    """The token goes only to the configured server (COGAMES_API_URL), never a constant."""
    seen = []

    def http_get(url, headers):
        seen.append(url)
        return json.dumps(CREDITS_BODY)

    env = {**env, SERVER_ENV: "https://other.example/api/"}
    SoftmaxClient(env, run=runner, http_get=http_get).credits()
    assert seen == [f"https://other.example/api{CREDITS_PATH}"]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(CREDITS_PATH, 403, "Forbidden", {}, None),
        urllib.error.URLError("no route"),
        "not json",
        json.dumps({"status": {}}),
    ],
)
def test_credits_read_failures_are_cli_errors_without_the_token(env, runner, failure):
    def http_get(url, headers):
        if isinstance(failure, Exception):
            raise failure
        return failure

    with pytest.raises(SoftmaxCliError) as caught:
        SoftmaxClient(env, run=runner, http_get=http_get).credits()
    assert json.loads(env["SOFTMAX_PLAYER_CREDENTIAL"])["token"] not in str(caught.value)


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


# --- submission state (A9): fixtures shaped like the live rows of 2026-10-09 ---

GOTA = "league_3c60897b-25cf-4b37-9d1a-8554c1198f28"


@pytest.mark.parametrize(
    "fixture, label, expected",
    [
        ("memberships_policy_champion.json", "games-bond-gota:v7", ("competing", True)),
        ("memberships_policy_benched.json", "games-bond-gota:v5", ("competing", False)),
        ("memberships_policy_disqualified.json", "games-bond-gota:v6", ("rejected", False)),
        ("memberships_policy_qualifying.json", "games-bond-gota:v8", ("pending", False)),
    ],
)
def test_submission_state_reads_our_membership(env, runner, fixture, label, expected):
    runner.on("coworld", "memberships", response=completed(synthetic(fixture)))
    state = make(env, runner, None).submission_state(PLAYER_ID, GOTA, label)
    assert (state.stage, state.is_champion) == expected and "membership" in state.detail
    assert runner.argvs() == [
        ["coworld", "memberships", "--league", GOTA, "--player", PLAYER_ID, "--policy", label,
         "--json"],
    ]  # fmt: skip


def test_submission_state_falls_back_to_the_submission(env, runner):
    """No membership yet: the submission's own status decides (recorded rows are ``placed``,
    which without a membership is ``unknown``)."""
    runner.on("coworld", "memberships", response=completed(synthetic("memberships_policy_none.json")))
    runner.on("coworld", "submissions", response=completed(recorded("submissions_league.json")))
    client = make(env, runner, None)
    [row, *_] = client.submissions(PLAYER_ID)
    assert row["submission_id"] == row["id"] and row["policy_version_id"].count(":v") == 1
    state = client.submission_state(PLAYER_ID, row["league_id"], row["policy_version_id"])
    assert state.stage == "unknown" and state.detail == "submission placed"
    assert client.submission_state(PLAYER_ID, GOTA, "never-submitted:v1").stage == "unknown"


def test_an_unknown_membership_status_is_unknown_not_pending(env, runner):
    """N2: only submitted and qualifying (metta PolicyMembershipStatus) count as pending."""
    page = json.loads(synthetic("memberships_policy_qualifying.json"))
    page["entries"][0]["status"] = "retired"
    runner.on("coworld", "memberships", response=completed(json.dumps(page)))
    state = make(env, runner, None).submission_state(PLAYER_ID, GOTA, "games-bond-gota:v8")
    assert state.stage == "unknown" and "retired" in state.detail


# --- the coach's answer to a permit ask (GET /observatory/v2/cogents/{player}/c/asks/{key}) ---

ASK = "permit-spend-8c5edbef63cc84a1-1"


@pytest.mark.parametrize(
    "fixture, answer",
    [
        ("cogent_ask_waiting.json", "waiting"),
        ("cogent_ask_yes.json", "yes"),
        ("cogent_ask_no.json", "no"),
        ("cogent_ask_used.json", "used"),
    ],
)
def test_permit_answer_reads_our_cogents_ask_record(env, runner, fixture, answer):
    seen = []

    def http_get(url, headers):
        seen.append((url, headers))
        return synthetic(fixture)

    client = SoftmaxClient(env, run=runner, http_get=http_get)
    assert client.permit_answer(ASK) == answer
    [(url, headers)] = seen
    assert url == f"{DEFAULT_SERVER}/observatory/v2/cogents/{PLAYER_ID}/c/asks/{ASK}"
    assert headers["Authorization"].startswith("Bearer ")
    assert runner.calls == []  # no CLI call: the read costs nothing but one GET


def test_permit_answer_is_none_when_it_cannot_be_read(env, runner):
    def missing(url, headers):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    assert SoftmaxClient(env, run=runner, http_get=missing).permit_answer(ASK) is None
    not_an_ask = SoftmaxClient(env, run=runner, http_get=lambda url, headers: '{"body": {}}')
    assert not_an_ask.permit_answer(ASK) is None

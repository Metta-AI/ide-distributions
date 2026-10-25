import json
from pathlib import Path

import pytest
from conftest import CREDIT_STDERR, LEAGUE_STDERR, PERMIT_STDERR, PLAYER, STARTER, STARTER_SHA

from play.__main__ import run
from play.games import GOTA
from play.journal import Journal
from play.limits import NAME_MAX, POLICY_NAME_MAX, SUFFIX_LENGTH
from play.program import PlayError

CHOSEN = "league_00000000-0000-0000-0000-000000000001"
SHORT = PLAYER.removeprefix("ply_")[:SUFFIX_LENGTH]
CHANGED = STARTER.decode().replace("self.abilityPoints = 0 then", "self.abilityPoints = 1 then")


def workspace(env) -> Path:
    return Path(env["PLAY_WORKSPACE"])


def first_version(program, name="towerless"):
    program.starter_fetch()
    program.program_change(CHANGED, "retreat earlier when hurt")
    return program.program_upload(name)


def test_starter_fetch_checks_the_published_sha256(program, cli, fetched, env):
    data = program.starter_fetch()
    assert data == {
        "starter": "Baseline",
        "player": "baseline",
        "sha256": STARTER_SHA,
        "size_bytes": len(STARTER),
        "lines": len(STARTER.splitlines()),
    }
    assert (workspace(env) / "starter/baseline.bas").read_bytes() == STARTER
    assert fetched == [
        f"https://example.invalid/api/observatory/v2/coworlds/cow_gota/player-files/{STARTER_SHA}"
    ]
    assert program.starter_fetch() == data  # safe to repeat
    assert cli.mutations() == []


def test_starter_fetch_refuses_bytes_that_do_not_match(program, env):
    with pytest.raises(PlayError) as error:
        program.starter_fetch("other")
    assert error.value.reason == "sha256_mismatch"
    assert not (workspace(env) / "starter/other.bas").exists()
    with pytest.raises(PlayError) as error:
        program.starter_fetch("missing")
    assert error.value.reason == "starter_not_found"
    assert error.value.details["available"] == ["baseline", "other"]


def test_the_cogent_changes_the_program_itself_through_the_check(program, env):
    program.starter_fetch()
    shown = program.program_show()
    assert shown["source"] == "starter" and shown["text"] == STARTER.decode()
    assert program.program_show("starter")["text"] == STARTER.decode()

    broken = "IF myHp() < 200 THEN\nNEXT i\nWHILE 1\ny = 2 @ 3\n"
    with pytest.raises(PlayError) as error:
        program.program_change(broken, "retreat earlier")
    assert error.value.reason == "program_invalid"
    problems = error.value.details["problems"]
    assert [p["line"] for p in problems] == [1, 2, 3, 4]
    assert "END IF" in problems[0]["problem"] and "NEXT" in problems[1]["problem"]
    assert "never closed with WEND" in problems[2]["problem"] and "'@'" in problems[3]["problem"]
    assert not (workspace(env) / "program.bas").exists()  # nothing was written

    data = program.program_change(CHANGED, "retreat earlier when hurt")
    assert (workspace(env) / "program.bas").read_text() == CHANGED
    assert data["lines_added"] == data["lines_removed"] == 1
    assert "+    if self.abilityPoints = 1 then" in data["diff"] and data["warnings"] == []

    typo = CHANGED.replace("attackTarget(bestId)", "atackTarget(bestId)")
    warned = program.program_change(typo, "typo")
    assert [w["line"] for w in warned["warnings"]] == [801]
    with pytest.raises(PlayError) as error:
        program.program_change(typo, "again")
    assert error.value.reason == "no_change"
    shown = program.program_show()
    assert shown["source"] == "program" and shown["pending_change"] == "typo"


def test_upload_uses_the_plain_name_and_one_name_per_program(program, cli, env):
    data = first_version(program)
    assert data == {
        "program": "towerless v1",
        "version": 1,
        "change": "retreat earlier when hurt",
        "already": False,
        "platform_name": "towerless",
    }
    upload = next(c for c in cli.calls if c[1] == "upload-policy")
    assert upload[4:] == ["--name", "towerless", "--title", "retreat earlier when hurt"]
    assert cli.calls[cli.calls.index(upload) - 1] == ["softmax", "status"]
    # The same file again is the same version; a change is the next version, same name.
    assert program.program_upload()["already"] is True
    program.program_change(CHANGED + "PRINT 1\n", "log once")
    assert program.program_upload()["program"] == "towerless v2"
    assert len(cli.mutations()) == 2
    assert (workspace(env) / "versions/towerless/v1.bas").read_text() == CHANGED
    shown = program.program_show("towerless v1")
    assert shown["program"] == "towerless v1" and shown["text"] == CHANGED


def test_a_taken_name_gets_the_platforms_short_suffix_and_is_never_shown(program, cli):
    cli.taken.add("towerless")
    data = first_version(program)
    assert data["program"] == "towerless v1"
    assert data["platform_name"] == f"towerless-{SHORT}"
    assert "ply_" not in json.dumps(data)
    longest = "a" * NAME_MAX
    assert len(f"{longest}-{SHORT}") == POLICY_NAME_MAX


def test_upload_refuses_bad_names_and_another_player(program, cli, env):
    program.starter_fetch()
    with pytest.raises(PlayError) as error:
        program.program_upload("towerless")
    assert error.value.reason == "no_program"
    program.program_change(CHANGED, "retreat earlier")
    for name in (None, "Bad Name", "a" * (NAME_MAX + 1)):
        with pytest.raises(PlayError) as error:
            program.program_upload(name)
        assert error.value.reason == "bad_arguments"
    cli.player = "ply_someone_else"
    envelope = run("program_upload", {"name": "towerless"}, program)
    assert envelope["ok"] is False and envelope["error"]["reason"] == "identity"
    assert cli.mutations() == []


def test_practice_defaults_to_five_games_with_a_human_title(program, cli):
    first_version(program)
    data = program.practice_request()
    assert data == {
        "program": "towerless v1",
        "outcome": "requested",
        "request_id": "xreq_1",
        "games": 5,
        "seats": [0],
    }
    body = json.loads(
        cli.inputs[cli.calls.index(["coworld", "xp-request", "create", "-", "--json"])]
    )
    assert body == {
        "target": {"league_id": GOTA.reference_league_id},
        "roster": [
            {"slot": 0, "player": {"policy_ref": "towerless:v1"}},
            *({"slot": seat, "player": {"random": True}} for seat in range(1, 10)),
        ],
        "num_episodes": 5,
        "private": True,
        "title": "Practice: towerless v1",
        "idempotency_key": "practice:towerless:v1:a1",
    }


@pytest.mark.parametrize("named", ["towerless v1", "towerless:v1", "Towerless V1", "towerless"])
def test_bare_names_resolve_inside_the_tool(program, cli, named):
    first_version(program)
    assert program.practice_request(named)["program"] == "towerless v1"
    body = json.loads(cli.inputs[-1])
    assert body["roster"][0]["player"] == {"policy_ref": "towerless:v1"}


def test_versions_this_workspace_never_uploaded_resolve_from_the_platform(program, cli):
    """An earlier Play appended the whole player id; the page and the user say "towerless v1"."""
    legacy = f"towerless-{PLAYER}"
    cli.policies += [
        {"name": legacy, "version": 1},
        {"name": f"tower-retreat-{SHORT}", "version": 1},
    ]
    assert program.practice_request("towerless v1")["program"] == "towerless v1"
    assert json.loads(cli.inputs[-1])["roster"][0]["player"] == {"policy_ref": f"{legacy}:v1"}
    assert program.practice_request("tower-retreat")["program"] == "tower-retreat v1"
    assert json.loads(cli.inputs[-1])["roster"][0]["player"] == {
        "policy_ref": f"tower-retreat-{SHORT}:v1"
    }
    assert program.practice_status()["summary"]["program"] == "tower-retreat v1"
    # The platform's lookup is this player's own versions, by exact name and version.
    assert f"mine=true&limit=200&player_id={PLAYER}&name_exact=towerless&version=1" in cli.api_reads
    data = program.program_release("towerless v1", True)
    assert data == {"program": "towerless v1", "outcome": "released", "already": False}
    assert [
        "coworld",
        "submit",
        f"{legacy}:v1",
        "--league",
        GOTA.reference_league_id,
        "--player",
        PLAYER,
        "--no-open-browser",
    ] in cli.calls
    with pytest.raises(PlayError) as error:
        program.practice_request("towerless v7")
    assert error.value.reason == "unknown_version"
    assert error.value.details["your_programs"] == ["tower-retreat", "towerless"]
    listed = program.program_versions()
    assert listed["all_programs"] == [
        {"program": "tower-retreat", "versions": [1]},
        {"program": "towerless", "versions": [1]},
    ]
    assert PLAYER not in json.dumps(listed) and SHORT not in json.dumps(listed)
    released = program.program_versions("towerless")
    assert released["versions"] == [
        {
            "version": 1,
            "program": "towerless v1",
            "change": "uploaded before this workspace kept versions",
            "practiced": [],
            "released": True,
        }
    ]


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
    first_version(program)
    cli.create = ("refuse", stderr)
    data = program.practice_request()
    assert data["outcome"] == "refused"
    assert {k: data[k] for k in expected} == expected
    assert len(cli.mutations()) == 2  # the upload and the one refused request


def test_practice_uses_the_league_the_coach_chose(program, cli, records):
    records.records[("state", "league")] = {
        "key": "league",
        "rev": 1,
        "body": {"leagueId": CHOSEN, "at": "x"},
    }
    game = {"name": "gota", "coworld_id": "cw_gota"}
    games = {GOTA.reference_league_id: game, CHOSEN: game}
    starter = program._fetch
    program._fetch = lambda url: (
        json.dumps({"game": games[url.rsplit("/", 1)[1]]}).encode()
        if "/leagues/" in url
        else starter(url)
    )
    first_version(program)
    program.practice_request()
    assert json.loads(cli.inputs[-1])["target"] == {"league_id": CHOSEN}
    program.program_release("towerless v1", True)
    assert [
        "coworld",
        "submit",
        "towerless:v1",
        "--league",
        CHOSEN,
        "--player",
        PLAYER,
        "--no-open-browser",
    ] in cli.calls


def test_practice_status_reports_every_game_in_plain_words(program, cli, env):
    first_version(program)
    program.practice_request(episodes=3)
    cli.episodes.append(("ereq_3", None, "failed", None, "BASIC compile error on line 4"))
    cli.xp_status = "running"
    running = program.practice_status()
    assert running["state"] == "running" and "summary" not in running
    assert running["plain"] == "Practice for towerless v1: 1 of 3 games finished so far."
    cli.xp_status = "completed"
    data = program.practice_status()
    won, timed_out, failed = data["games"]
    assert won == {
        "game": 1,
        "result": "won",
        "why": "Red's side destroyed the other fort after 20.1 minutes",
        "minutes": 20.1,
        "your_glory": 116,
        "your_xp": 2327,
        "other_heroes_xp": 5049,
        "watch_url": "https://example.invalid/observatory/episodes/ep-1",
    }
    assert timed_out["result"] == "no winner" and timed_out["your_glory"] == 0
    assert "time ran out after 21.7 minutes" in timed_out["why"]
    assert failed == {
        "game": 3,
        "result": "failed",
        "why": "the game did not finish: BASIC compile error on line 4",
        "watch_url": None,
    }
    summary = data["summary"]
    assert summary["plain"].startswith(
        "towerless v1 played 3 practice games: won 1, ran out of time 1, failed to run 1. "
        "Your hero averaged 2,666 XP and 58 glory."
    )
    assert "time near enemy towers" in summary["not_measured"]
    assert "ply_" not in json.dumps(data)
    assert cli.mutations()[-1][2] == "create"  # reading results mutates nothing

    # The next version's practice is compared with this one.
    program.program_change(CHANGED + "PRINT 1\n", "log once")
    program.program_upload()
    cli.episodes = [("ereq_9", "ep-9", "completed", dict(cli.episodes[0][3]), None)]
    program.practice_request()
    later = program.practice_status()["summary"]
    assert later["previous"] == {
        "program": "towerless v1",
        "games": 3,
        "won": 1,
        "average_xp": 2666,
        "average_glory": 58,
    }
    assert "Last practice of towerless v1: won 1 of 3" in later["plain"]
    versions = program.program_versions()["versions"]
    assert versions[0]["practiced"][0].startswith("towerless v1 played 3 practice games")


def test_practice_status_waits_for_the_games(program, cli):
    first_version(program)
    program.practice_request()
    states = iter(["running", "running", "completed"])
    waited = []

    def sleep(seconds):
        waited.append(seconds)
        cli.xp_status = next(states)

    program._sleep = sleep
    cli.xp_status = "running"
    data = program.practice_status(wait_seconds=480)
    assert data["state"] == "completed" and len(waited) == 3


def test_practice_status_treats_not_produced_as_terminal(program, cli):
    first_version(program)
    program.practice_request()
    cli.replay_state = "not_produced"
    assert "summary" in program.practice_status()
    cli.replay_state = "pending"
    assert "results_pending" in program.practice_status()


def test_release_needs_the_users_yes_for_one_version_and_submits_once(program, cli, env):
    first_version(program)
    for arguments in (
        {"program": "towerless v1", "user_said_yes": False},
        {"program": "towerless", "user_said_yes": True},
    ):
        envelope = run("program_release", arguments, program)
        assert envelope["error"]["reason"] == "bad_arguments"
    upload = next(c for c in cli.calls if c[1] == "upload-policy")
    submitted = upload[upload.index("--name") + 1]

    def membership(row_id, name, version, owner=PLAYER):
        # The platform's shape: the name only under `policy`, the label `<name>:v<N>`.
        policy = {
            "id": f"pv_{row_id}",
            "label": f"{name}:v{version}",
            "policy": {"id": f"p_{name}", "name": name},
            "version": version,
            "player_id": owner,
        }
        return {"id": row_id, "status": "competing", "policy_version": policy}

    cli.memberships = [
        membership("m_old", submitted, 0),
        membership("m_other", "rival", 1, owner="ply_someone_else"),
        membership("m_new", submitted, 1),
    ]
    data = program.program_release("towerless v1", True)
    assert data == {
        "program": "towerless v1",
        "outcome": "released",
        "already": False,
        "replaced": ["towerless v0"],
    }
    assert cli.retired == ["m_old"]  # not another player's, not the new version
    cli.memberships = [cli.memberships[2]]
    cli.submissions = [
        {
            "id": "sub_1",
            "policy_version": {"label": "towerless:v1"},
            "league": {"id": GOTA.reference_league_id},
            "status": "pending",
        }
    ]
    cli.calls.clear()
    assert program.program_release("towerless v1", True)["already"] is True
    assert cli.mutations() == []
    journal = Journal.load(workspace(env))
    assert journal.versions[0].release.submission_id == "sub_1"
    assert program.program_versions()["versions"][0]["released"] is True


@pytest.mark.parametrize(
    "stderr, refusal",
    [
        (PERMIT_STDERR, "permit_required"),
        (LEAGUE_STDERR, "league_not_chosen"),
        (CREDIT_STDERR, "credit_denied"),
    ],
)
def test_release_refusals_are_typed(program, cli, stderr, refusal):
    first_version(program)
    cli.submit = ("refuse", stderr)
    data = program.program_release("towerless v1", True)
    assert data["outcome"] == "refused" and data["refusal"] == refusal
    assert len(cli.mutations()) == 2
    if refusal == "credit_denied":
        assert data["reason"] == "no credits left"


def test_one_task_card_per_request_in_the_records_the_mind_reads(program, records):
    with pytest.raises(PlayError) as error:
        program.task_update("change", "running", "Editing")
    assert error.value.reason == "no_card"
    program.task_update(
        "understand",
        "done",
        "You want your hero to stay away from enemy towers.",
        title="Run under enemy towers less",
    )
    program.task_update("change", "running", "Changing when the hero retreats")
    data = program.task_update("upload", "done", "Saved as towerless v2", version=2)
    assert data == {
        "card": "Run under enemy towers less",
        "step": "Save it as a new version",
        "status": "done",
        "finished": False,
    }
    ((key, card),) = [(k, v) for (c, k), v in records.records.items() if c == "tasks"]
    assert key.startswith("play-") and card["rev"] == 3
    body = card["body"]
    assert body["origin"] == "player"  # the bridge forwards only coach tasks
    assert body["state"] == "active" and body["version"] == 2
    assert [s["rung"] for s in body["steps"]] == [
        "reflect",
        "implement",
        "compile",
        "tryout",
        "screen",
        "submit",
    ]
    assert [s["status"] for s in body["steps"]] == [
        "ok",
        "running",
        "ok",
        "pending",
        "pending",
        "pending",
    ]
    assert [p["base_rev"] for p in records.puts] == [None, 1, 2]
    assert len({p["idempotency_key"] for p in records.puts}) == 3

    program.task_update(
        "release", "skipped", "You chose not to release yet", outcome="towerless v2 won 3 of 5"
    )
    body = records.records[("tasks", key)]["body"]
    assert (body["state"], body["status"], body["outcome"]) == (
        "done",
        "kept",
        "towerless v2 won 3 of 5",
    )

    program.task_update("understand", "done", "Practice again", title="Practice towerless v2")
    program.task_update("understand", "done", "Something else", title="Release towerless v2")
    cards = {k: v["body"] for (c, k), v in records.records.items() if c == "tasks"}
    assert [b["status"] for b in cards.values()] == ["kept", "dropped", "active"]


def test_bad_arguments_never_reach_the_platform(program, cli):
    for operation, arguments in [
        ("practice_request", {"program": "Towerless!"}),
        ("practice_request", {"seats": [10]}),
        ("practice_request", {"episodes": 21}),
        ("practice_status", {"request_id": "../etc"}),
        ("practice_status", {"wait_seconds": 9999}),
        ("program_release", {"program": "p v1"}),
        ("program_release", {"program": "p v1", "user_said_yes": True, "league": "other"}),
        ("program_change", {"text": "x = 1\n", "change": " "}),
        ("task_update", {"step": "deploy", "status": "done", "detail": "x"}),
        ("promotion_submit", {}),
    ]:
        envelope = run(operation, arguments, program)
        assert envelope["ok"] is False and envelope["error"]["reason"] == "bad_arguments", operation
    assert cli.calls == []


def test_a_name_another_player_of_the_user_owns_counts_as_taken(program, cli):
    cli.siblings.append({"name": "towerless", "version": 1})
    cli.policies.append({"name": "towerless-other", "version": 1})
    data = first_version(program)
    assert data["platform_name"] == f"towerless-{SHORT}"
    assert data["version"] == 1  # a new policy of this player, not v2 of the sibling's
    assert any("player_id=" + PLAYER in q for q in cli.api_reads)


def test_small_edits_apply_once_and_go_through_the_same_check(program, env):
    program.starter_fetch()
    data = program.program_edit(
        [{"find": "self.abilityPoints = 0 then", "replace": "self.abilityPoints = 1 then"}],
        "spend points sooner",
    )
    assert (workspace(env) / "program.bas").read_text() == CHANGED
    assert data["lines_added"] == data["lines_removed"] == 1
    again = program.program_edit(
        [{"find": "= 1 then\n      exit sub", "replace": "= 2 then\n      exit sub"}], "again"
    )
    assert "= 2 then" in (workspace(env) / "program.bas").read_text() and again["warnings"] == []


@pytest.mark.parametrize(
    ("edits", "reason"),
    [
        ([{"find": "no such text anywhere", "replace": "x"}], "edit_not_found"),
        ([{"find": "then", "replace": "THEN"}], "edit_ambiguous"),
        ([{"find": "end if", "replace": ""}, {"find": "wend", "replace": ""}], None),
        ([{"find": "", "replace": "x"}], "bad_arguments"),
    ],
)
def test_a_bad_edit_writes_nothing(program, env, edits, reason):
    program.starter_fetch()
    with pytest.raises(PlayError) as error:
        program.program_edit(edits, "bad")
    if reason is not None:
        assert error.value.reason == reason
    assert not (workspace(env) / "program.bas").exists()


def test_an_edit_that_breaks_the_program_is_refused_by_the_check(program, env):
    program.starter_fetch()
    with pytest.raises(PlayError) as error:
        program.program_edit(
            [{"find": "if bestClass >= 0 then", "replace": "if bestClass >= 0"}], "drop an end if"
        )
    assert error.value.reason == "program_invalid"
    assert not (workspace(env) / "program.bas").exists()


def test_who_we_are_reads_the_public_cogent_profile(program, records):
    records.records.pop(("state", "league"))
    urls = []

    def profile(url):
        urls.append(url)
        return json.dumps({"name": "scissors", "owner_name": "Emmett S"}).encode()

    program._fetch = profile
    assert program.who_we_are() == {
        "named": False,
        "cogent_name": None,
        "user_name": "Emmett S",
        "coworld": None,
    }
    assert urls == [f"{program.client._cli.server}/observatory/v2/cogents/{PLAYER}"]


def test_who_we_are_has_no_user_name_when_the_platform_has_none(program):
    program._fetch = lambda url: json.dumps({"name": "scissors", "owner_name": None}).encode()
    assert run("who_we_are", {}, program) == {
        "ok": True,
        "data": {"named": False, "cogent_name": None, "user_name": None, "coworld": None},
    }


def test_who_we_are_reports_an_unreachable_profile(program):
    import urllib.error

    def refuse(url):
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    program._fetch = refuse
    assert run("who_we_are", {}, program)["error"]["reason"] == "who_unavailable"

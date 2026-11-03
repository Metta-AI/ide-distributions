"""Step 3: the champion record follows the platform every ten minutes (design B.6, A4.9)."""

from __future__ import annotations

import json

from research_core.platform.fake import (
    COWORLD_NAME,
    LEAGUE_ID,
    LEAGUELESS_COWORLD_ID,
    OLD_COWORLD_ID,
)
from research_core.store import champion
from research_core.store.db import Store
from research_core.watcher import champion_sync, wakes
from tests.watcher._support import advance, events_of, make_ctx
from tests.watcher._support_w3 import platform_with_faults


def test_empty_league_writes_nothing_on_a_fresh_workspace(tmp_path):
    ctx = make_ctx(tmp_path)
    report = champion_sync.run(ctx)
    assert report == {"leagues": [LEAGUE_ID], "changed": []}
    assert champion.read(ctx.store, LEAGUE_ID) is None
    assert events_of(ctx.store, "champion.changed") == []
    assert ctx.store.get_meta("champion_synced_at") is not None


def test_champion_change_outside_the_workspace_is_detected_once(tmp_path):
    ctx = make_ctx(tmp_path)
    champion_sync.run(ctx)
    platform_with_faults(ctx, "champion=pv_external")  # the user crowned a policy on the site

    assert champion_sync.run(ctx)["skipped"] == "synced recently"
    advance(ctx, 600)
    report = champion_sync.run(ctx)

    assert report["changed"] == [
        {"league_id": LEAGUE_ID, "state": "champion", "policy_version_id": "pv_external"}
    ]
    record = champion.read(ctx.store, LEAGUE_ID)
    assert record["state"] == "champion" and record["policy_version_id"] == "pv_external"
    assert record["membership_id"] and record["division_id"] == "div_fake"
    [event] = events_of(ctx.store, "champion.changed")
    assert event["payload"]["policy_version_id"] == "pv_external"
    assert event["payload"]["previous"] is None

    advance(ctx, 600)
    assert champion_sync.run(ctx)["changed"] == []
    assert len(events_of(ctx.store, "champion.changed")) == 1


def test_own_submission_is_followed_through_its_states(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.platform.configure(polls_to_complete=3)
    uploaded = ctx.platform.upload_file_policy(
        name="tuned", content=json.dumps({"parameters": {"aggression": 4, "patience": 5}}).encode()
    )
    ctx.platform.submit(league_id=LEAGUE_ID, policy_version_id=uploaded.id, auto_champion="never")

    report = champion_sync.run(ctx, force=True)
    assert report["changed"][0]["state"] == "pending"
    ctx.platform.memberships(mine=True)  # two more polls place the submission
    ctx.platform.memberships(mine=True)
    report = champion_sync.run(ctx, force=True)
    assert report["changed"] == [
        {"league_id": LEAGUE_ID, "state": "placed", "policy_version_id": uploaded.id}
    ]
    states = [e["payload"]["state"] for e in events_of(ctx.store, "champion.changed")]
    assert states == ["pending", "placed"]


def test_platform_down_leaves_the_stamp_for_a_retry(tmp_path):
    ctx = make_ctx(tmp_path)
    platform_with_faults(ctx, "platform=down")
    report = champion_sync.run(ctx)
    assert "error" in report and "down" in report["error"]
    assert ctx.store.get_meta("champion_synced_at") is None


def _activate(ctx, coworld_id):
    """Make ``coworld_id`` the active Coworld version, with its own Coworld store."""
    ctx.coworld = ctx.ws.coworld(coworld_id)
    ctx.coworld.ensure_dirs()
    ctx.store = Store.open(ctx.coworld.store_path, kind="coworld", clock=ctx.clock)


def _notices(ctx):
    return [row["text"] for row in ctx.wstore.q("SELECT text FROM notices ORDER BY id")]


def test_leagues_of_joins_by_coworld_name_in_any_version(tmp_path):
    ctx = make_ctx(tmp_path)
    assert [league.id for league in champion_sync.leagues_of(ctx, COWORLD_NAME)] == [LEAGUE_ID]
    assert champion_sync.leagues_of(ctx, "leaguelessgame") == []


def test_a_workspace_behind_its_leagues_version_still_syncs_and_says_so_once(tmp_path):
    # 2026-11-03: the workspace pinned generals-competition 0.6.3 while its leagues followed the
    # canonical 0.7.3; joining by version id synced nothing and said only nothing_to_sync.
    ctx = make_ctx(tmp_path)
    _activate(ctx, OLD_COWORLD_ID)  # fakegame version 0; the league runs version 1
    platform_with_faults(ctx, "champion=pv_external")

    report = champion_sync.run(ctx)
    for _ in range(3):
        advance(ctx, 600)
        champion_sync.run(ctx)

    assert report["leagues"] == [LEAGUE_ID]
    assert report["changed"] == [
        {"league_id": LEAGUE_ID, "state": "champion", "policy_version_id": "pv_external"}
    ]
    assert champion.read(ctx.store, LEAGUE_ID)["policy_version_id"] == "pv_external"
    assert _notices(ctx) == [
        "A newer version of the Coworld fakegame exists: the workspace is on version"
        " 0 (cow_fake_old) and is behind its leagues, which run version 1 (cow_fake). Champion"
        " sync still follows those leagues. The workspace's policies may need checking on"
        " version 1 (cow_fake)."
    ]
    assert ctx.store.get_meta("league_versions") == (
        "The workspace is on fakegame version 0 (cow_fake_old); its leagues run 1 (cow_fake)"
    )


def test_a_coworld_no_league_runs_starts_no_wake_and_says_so_once(tmp_path):
    ctx = make_ctx(tmp_path)
    platform_with_faults(ctx, "champion=pv_elsewhere")  # the Player's champion in fakegame
    _activate(ctx, LEAGUELESS_COWORLD_ID)

    reports = [champion_sync.run(ctx)]
    for _ in range(3):
        advance(ctx, 600)
        reports.append(champion_sync.run(ctx))
    wake_report = wakes.run(ctx)

    assert (
        reports
        == [
            {
                "leagues": [],
                "changed": [],
                "nothing_to_sync": "no league runs the Coworld leaguelessgame",
            }
        ]
        * 4
    )
    assert events_of(ctx.store, "champion.changed") == []
    assert wake_report["started"] == [] and ctx.hermes.calls == []
    assert _notices(ctx) == [
        "No league runs the Coworld leaguelessgame (the workspace is on version 1"
        " (cow_leagueless)), so champion sync has nothing to follow. It follows the leagues"
        " again once a league for leaguelessgame exists."
    ]
    assert ctx.store.get_meta("league_versions") == (
        "No league runs the Coworld leaguelessgame; the workspace is on version 1 (cow_leagueless)"
    )


def test_champions_in_other_coworlds_leagues_start_no_wake(tmp_path):
    # 2026-11-02: no league matched the workspace's pinned version id, so the sync fell back to
    # every league the Player was in and raised one champion.changed, so one wake, per league.
    ctx = make_ctx(tmp_path)
    platform_with_faults(ctx, "champion=pv_elsewhere")  # the Player's champion in fakegame
    _activate(ctx, LEAGUELESS_COWORLD_ID)  # another Coworld: fakegame's league is not its own

    champion_sync.run(ctx)
    wakes.run(ctx)

    assert ctx.store.get_meta("champion_synced_at") is not None
    assert champion.read(ctx.store, LEAGUE_ID) is None
    assert ctx.hermes.calls == []
    # The champion is real: the sync left it alone because its league belongs to fakegame.
    [crowned] = ctx.platform.memberships(mine=True, champions_only=True)
    assert crowned.league_id == LEAGUE_ID


def test_matching_versions_show_no_version_line(tmp_path):
    ctx = make_ctx(tmp_path)
    champion_sync.run(ctx)
    assert ctx.store.get_meta("league_versions") is None
    assert _notices(ctx) == []

"""Step 3: the champion record follows the platform every ten minutes (design B.6, A4.9)."""

from __future__ import annotations

import json

from research_core.platform.fake import LEAGUE_ID
from research_core.store import champion
from research_core.watcher import champion_sync
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


def test_leagues_of_prefers_the_coworld_s_own_leagues(tmp_path):
    ctx = make_ctx(tmp_path)
    assert [league.id for league in champion_sync.leagues_of(ctx)] == [LEAGUE_ID]
    ctx.coworld = ctx.ws.coworld("cow_other")  # no league names it: every league is checked
    assert [league.id for league in champion_sync.leagues_of(ctx)] == [LEAGUE_ID]

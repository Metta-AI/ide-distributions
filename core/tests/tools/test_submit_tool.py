"""The ``submit`` tool: user requests never refused, the autonomous quality bar, the ladder."""

from __future__ import annotations

import pytest

from research_core import gitops
from research_core.platform import submission
from research_core.store import capabilities, champion, experiments, notices, settings
from tests.tools.test_version_tool import HARNESS, Lab, commit_policy

OVERRIDE = "a user request overrides this"


@pytest.fixture
def lab(ctx_factory) -> Lab:
    return Lab(ctx_factory)


def set_setting(lab: Lab, key: str, value) -> None:
    store = lab.wstore if settings.spec(key).scope == "workspace" else lab.store
    settings.update(store, HARNESS, key, value, cites="msg-0")


def unmerged_version(lab: Lab, summary="Unmerged", aggression=4) -> dict:
    """An uploaded Version built on an Orchestrator worktree that main does not contain."""
    orch = gitops.worktree_add(lab.code, lab.coworld.worktrees / "orch-1", branch="orch/1")
    commit_policy(orch, aggression, 5, "Not on main")
    record = lab.create(summary, worktree="orch-1")
    return lab.upload(record["id"])["record"]


def merged_version(lab: Lab, summary="Merged", patience=7) -> dict:
    """An uploaded Version built from a commit on main (distinct bytes per patience)."""
    commit_policy(lab.code, 3, patience, "On main")
    record = lab.create(summary)
    return lab.upload(record["id"])["record"]


def confirm_experiment(lab: Lab, candidate: str, baseline: str, effect: float) -> dict:
    created = experiments.create(
        lab.store,
        HARNESS,
        summary=f"{candidate} against {baseline}",
        hypothesis={
            "statement": "patience lowers errors",
            "if_true": "fewer errors",
            "if_false": "the same errors",
            "motivation": "the mechanics doc",
        },
        methods={
            "baseline": baseline,
            "candidate": candidate,
            "procedure": "paired seeds",
            "metrics": [{"name": "score", "direction": "up", "role": "primary"}],
            "decision_rule": {"text": "mean signed diff above 0", "n_min": 1},
        },
    )
    experiments.start(lab.store, HARNESS, created["id"])
    experiments.add_run(
        lab.store, HARNESS, created["id"], kind="xp", platform_request_id="xreq_1", arm="pair"
    )
    return experiments.close(
        lab.store,
        HARNESS,
        created["id"],
        verdict="confirmed",
        measurements=[
            {
                "metric": "score",
                "baseline": 50.0,
                "candidate": 50.0 + effect,
                "n": 10,
                "effect": effect,
            }
        ],
        interpretation="the candidate is better",
        reason_codes=["primary_significant"],
    )


# --- basics --------------------------------------------------------------------------------


def test_submit_requires_an_uploaded_version(lab):
    lab.create("Built only")
    result = lab.submit("V-1", user_requested=True, cites="msg-1")
    assert result["ok"] is False and "not uploaded" in result["error"]
    researcher = lab.run("submit", {"version": "V-1", "league": "league_fake"}, role="researcher")
    assert researcher["ok"] is False and "unknown tool" in researcher["error"]


def test_submit_by_league_name_and_unknown_league(lab):
    merged_version(lab)
    by_name = lab.run(
        "submit", {"version": "V-1", "league": "fake league", "user_requested": True, "cites": "m"}
    )
    assert by_name["ok"] is True and by_name["league"] == {
        "id": "league_fake",
        "name": "Fake League",
    }
    unknown = lab.run("submit", {"version": "V-1", "league": "league_nope", "user_requested": True})
    assert unknown["ok"] is False and "Fake League (league_fake)" in unknown["error"]


# --- the user's request ----------------------------------------------------------------------


def test_submit_user_request_never_refused(lab):
    record = unmerged_version(lab)
    set_setting(lab, "paused", True)
    set_setting(lab, "submissions.mode", "ask")
    result = lab.submit(record["id"], user_requested=True, cites="msg-7", reason="the user asked")
    assert result["ok"] is True, result
    warnings = result["warnings"]
    assert any("paused" in w for w in warnings)
    assert any("submissions.mode is ask" in w for w in warnings)
    assert any("not merged into main" in w for w in warnings)
    assert not any(OVERRIDE in w for w in warnings)
    assert result["status"] == "champion" and result["record"]["submission_status"] == "champion"
    assert result["submission"]["policy_version_id"] == record["uploaded"]["policy_version_id"]
    assert result["champion"]["version_id"] == 1 and result["champion"]["state"] == "champion"
    assert result["champion"]["reason"] == "the user asked"
    (event,) = lab.events("version.submitted")
    assert event["autonomous"] is False and event["cites"] == "msg-7"
    assert event["league"] == "league_fake" and event["submission_id"] == result["submission"]["id"]
    (notice,) = notices.unread(lab.wstore)
    assert notice["text"] == "Submitted V-1 to Fake League (champion)" and notice["refs"] == ["V-1"]
    assert "note" not in result

    # cites is a record, never a gate (A4.5): the result asks for it.
    second = merged_version(lab, "Second")
    without = lab.submit(second["id"], user_requested=True, auto_champion="never")
    assert without["ok"] is True and "cites" in without["note"]
    assert without["status"] == "competing"


# --- the autonomous quality bar -------------------------------------------------------------


def test_submit_autonomous_quality_bar(lab):
    unmerged = unmerged_version(lab)
    refused = lab.submit(unmerged["id"])
    assert refused["ok"] is False
    reasons = refused["problems"][1:]
    assert reasons and all(r.endswith(OVERRIDE) for r in reasons)
    assert any("V-1 (commit" in r and "not merged into main" in r for r in reasons)
    assert (
        lab.events("version.submitted") == [] and lab.platform().snapshot()["counts"]["submit"] == 0
    )

    # A champion exists: a confirmed Experiment against it is needed.
    starter = merged_version(lab, "Starter", patience=6)
    assert lab.submit(starter["id"], user_requested=True, cites="msg-1")["status"] == "champion"
    candidate = merged_version(lab, "Candidate", patience=8)
    no_experiment = lab.submit(candidate["id"])
    assert no_experiment["ok"] is False
    assert any(
        f"no confirmed Experiment compares {candidate['id']} with the champion {starter['id']}" in r
        for r in no_experiment["problems"]
    )
    confirm_experiment(lab, candidate["id"], starter["id"], effect=1.0)
    set_setting(lab, "submissions.min_margin_over_champion", 2.0)
    short = lab.submit(candidate["id"])
    assert short["ok"] is False and any("at least 2.0" in r for r in short["problems"])
    set_setting(lab, "submissions.min_margin_over_champion", 0.5)
    allowed = lab.submit(candidate["id"], reason="confirmed by X-1")
    assert allowed["ok"] is True, allowed
    assert allowed["status"] == "champion" and allowed["champion"]["version_id"] == 3
    (_, event) = lab.events("version.submitted")
    assert event["autonomous"] is True and event["cites"] is None
    assert any("Submitted V-3 to Fake League" in n["text"] for n in notices.unread(lab.wstore))


def test_submit_autonomous_without_champion_runs_the_check(lab):
    merged = merged_version(lab)
    assert champion.read(lab.store, "league_fake") is None
    result = lab.submit(merged["id"], auto_champion="never")
    assert result["ok"] is True, result
    assert result["status"] == "competing"
    logs = sorted((lab.coworld.documents / "logs").glob("check-*.log"))
    assert len(logs) == 1 and "aggression" in logs[0].read_text()
    assert capabilities.read(lab.store, "check") is None
    assert not list(lab.coworld.worktrees.glob("*"))  # the temporary worktree is gone


def test_submit_autonomous_check_failure_is_a_reason_and_reports_the_capability(lab):
    commit_policy(lab.code, 3, 8, "A policy")
    record = lab.create("Checked")
    lab.upload(record["id"])
    # The next commit carries a broken check command and another policy.
    path = lab.code / "mixin" / "MIXIN.md"
    path.write_text(
        path.read_text().replace("  check: python3 mixin/tools/check.py\n", "  check: exit 7\n")
    )
    commit_policy(lab.code, 3, 9, "Break the check")
    broken = lab.create("Broken check")
    lab.upload(broken["id"])
    result = lab.submit(broken["id"])
    assert result["ok"] is False
    assert any("failed with exit code 7" in r for r in result["problems"])
    assert capabilities.read(lab.store, "check")["status"] == "broken"
    # The earlier Version's commit has a working check and passes.
    assert lab.submit(record["id"], auto_champion="never")["ok"] is True


def test_submit_autonomous_window_count_mode_and_pause(lab):
    first = merged_version(lab, "First", patience=6)
    second = merged_version(lab, "Second", patience=7)
    set_setting(lab, "submissions.max_count", 1)
    assert lab.submit(first["id"], auto_champion="never")["ok"] is True
    capped = lab.submit(second["id"], auto_champion="never")
    assert capped["ok"] is False
    assert any("1 of 1 autonomous submissions" in r for r in capped["problems"])
    set_setting(lab, "submissions.max_count", None)
    set_setting(lab, "submissions.until", "2000-01-01T00:00:00Z")
    expired = lab.submit(second["id"], auto_champion="never")
    assert any("window ended at 2000-01-01T00:00:00Z" in r for r in expired["problems"])
    set_setting(lab, "submissions.until", None)
    set_setting(lab, "submissions.mode", "ask")
    set_setting(lab, "paused", True)
    held = lab.submit(second["id"], auto_champion="never")
    assert any("submissions.mode is ask" in r for r in held["problems"])
    assert any("workspace is paused" in r for r in held["problems"])
    set_setting(lab, "submissions.mode", "autonomous")
    set_setting(lab, "paused", False)
    set_setting(lab, "submissions.quality_bar", False)
    unmerged = unmerged_version(lab, "Unmerged")
    assert lab.submit(unmerged["id"], auto_champion="never")["ok"] is True


# --- the ladder ----------------------------------------------------------------------------


def test_submission_status_ladder(lab):
    record = lab.create("Starter")
    assert record["submission_status"] == "built"
    assert lab.read("V-1")["record"]["submission_status"] == "built"
    uploaded = lab.upload("V-1")["record"]
    assert uploaded["submission_status"] == "uploaded"
    policy_version_id = uploaded["uploaded"]["policy_version_id"]

    # Each platform read advances a pending submission one poll; four reads to be placed.
    lab.platform().configure(polls_to_complete=4)
    submitted = lab.submit("V-1", user_requested=True, cites="msg-1", auto_champion="never")
    assert submitted["ok"] is True and submitted["status"] == "submitted"
    assert submitted["submission"]["status"] == "pending"
    assert champion.read(lab.store, "league_fake")["state"] == "pending"
    competing = lab.read("V-1")
    assert competing["record"]["submission_status"] == "competing"
    assert competing["platform"]["memberships"][0]["status"] == "competing"

    # Cached for a minute within one process; a write invalidates.
    platform = lab.platform(faults=f"champion={policy_version_id}")
    assert submission.status(lab.store, platform, competing["record"]) == "competing"
    submission.invalidate(policy_version_id)
    assert submission.status(lab.store, platform, competing["record"]) == "champion"
    assert lab.read("V-1")["record"]["submission_status"] == "champion"
    assert submission.status_from_states([], []) == "uploaded"


def test_status_falls_back_to_the_champion_record_when_the_platform_is_down(lab):
    lab.create("Starter")
    lab.upload("V-1")
    lab.submit("V-1", user_requested=True, cites="msg-1")
    down = lab.run("version", {"action": "read", "version": "V-1"}, faults="platform=down")
    assert down["ok"] is True
    assert down["record"]["submission_status"] == "champion"
    assert "unavailable" in down["platform"]

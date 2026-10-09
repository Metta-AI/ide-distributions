"""queue-keeper end to end (Plan 01 Task 3.9): the real ``queue_keeper.run`` over a
bootstrapped canonical with a bare ``origin``, a FileQueue board, FakeSoftmax and the
fakegame, with scripted card workers (``tests/support/keeper_host.py``)."""

import dataclasses
import datetime
import re
import types

import pytest
import yaml
from support.keeper_host import GAME_VERSION, PROFILE, Host, evaluating_experiment, launch_all

from autoresearch.comms.publisher import PublishResult, publish_runtime
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.card import CardSpec
from autoresearch.runtime import promotion, proposals, queue_keeper, xp_poll
from autoresearch.runtime.ledger import canonical_lock
from autoresearch.runtime.markers import review_marker
from autoresearch.runtime.queue_keeper import report_skipped
from autoresearch.runtime.signals import KEEPER_STEP_FAILED
from autoresearch.softmax_tool.protocol import SubmissionState

T0 = datetime.datetime.now(datetime.UTC).replace(minute=5, second=0, microsecond=0)


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    return Host(tmp_path)


def _help_requests(host: Host, text: str = "") -> list[dict]:
    """Help requests whose title contains ``text`` or whose ``about`` names it."""
    return [
        m
        for m in host.outbox()
        if m["kind"] == "help_request" and (text in m["title"] or text in m.get("about", {}).values())
    ]


def _subsequence(patterns: list[str], subjects: list[str]) -> bool:
    """True if each pattern matches a subject, in order."""
    remaining = iter(subjects)
    return all(any(re.search(p, s) for s in remaining) for p in patterns)


# --- identity failure on the first tick (C6, C7) ---


def test_whoami_blips_skip_runs_then_pause_once_per_streak(tmp_path):
    """I1: a whoami failure that is not a rejection skips the run (no pause, no help request,
    no admission); the third in a row pauses with one help request for the whole streak; a
    good whoami resumes."""
    host = Host(tmp_path)
    host.tick(at(0))  # bootstrap + a planning request
    host.set_faults("fail_after=whoami")
    for minute in (1, 2):
        blip = host.tick(at(minute))
        assert blip.preflight.skipped and not blip.paused and blip.failures == {}
        assert not (host.ledger / "pause.json").exists()
        assert host.queue.list() == []  # nothing admitted while whoami is unavailable
    assert _help_requests(host) == []

    third = host.tick(at(3))
    assert third.paused and (host.ledger / "pause.json").exists()
    [help_request] = _help_requests(host, "Agent paused")
    assert "3 times in a row" in help_request["body"]
    assert help_request["runtime_key"].startswith("whoami-unavailable:")
    for minute in (4, 5):
        assert host.tick(at(minute)).paused
    assert len(_help_requests(host, "Agent paused")) == 1  # one per streak

    host.set_faults("")
    assert not host.tick(at(6)).paused
    assert not (host.ledger / "pause.json").exists()


def test_rejected_credential_pauses_on_the_first_tick(tmp_path):
    host = Host(tmp_path, faults="identity=rejected")
    first = host.tick(at(0))
    assert first.paused and not first.preflight.skipped
    [help_request] = _help_requests(host, "Agent paused")
    assert "did not accept" in help_request["body"]


def test_identity_failure_on_the_first_tick_pauses_and_asks_for_help(tmp_path):
    host = Host(tmp_path, faults="player=ply_someone_else")

    first = host.tick(at(0))

    assert first.bootstrap.ok and first.paused and not first.preflight.ok
    assert host.queue.list() == [] and host.requests() == []  # no admission, no requests
    pause = host.committed(host.ledger / "pause.json")
    assert "ply_someone_else" in pause["reason"]
    [help_request] = _help_requests(host, "Agent paused")
    assert help_request["from"] == "runtime" and help_request["priority"] == "P0"
    assert host.remote_head() == host.head()

    second = host.tick(at(1))  # still failing: no second help_request
    assert second.paused and len(_help_requests(host, "Agent paused")) == 1

    host.set_faults("")
    third = host.tick(at(2))

    assert not third.paused and third.failures == {}
    assert not (host.ledger / "pause.json").exists()
    assert any(m["title"].startswith("Agent resumed") for m in host.outbox())
    assert third.planning is not None  # the cadence asks for planning now
    host.tick(at(3))
    [card] = host.queue.list()
    assert card.spec.mode == "planning" and card.assignee == PROFILE


def test_tick_snapshots_hermes_memory_into_canonical(host):
    host.tick(at(0))
    memory = host.home / "memories" / "MEMORY.md"
    memory.parent.mkdir(exist_ok=True)
    memory.write_text("lesson one\n")

    run = host.tick(at(1))

    assert run.failures == {}
    assert gitops.show(host.canonical, "HEAD", ".supervisor/hermes-memory.md") == b"lesson one\n"
    assert host.remote_head() == host.head()  # pushed with main


# --- the full scripted timeline ---


def test_full_timeline(host):
    # Tick 1: bootstrap seeds and pushes; nothing to admit; the cadence asks for planning.
    run1 = host.tick(at(0))
    assert run1.bootstrap.board_created and run1.failures == {}, run1.failures
    assert run1.planning is not None and host.queue.list() == []
    planning_request = run1.planning.request_id
    assert host.remote_head() == host.head() and host.clean()

    # Tick 2: the planning request is admitted and assigned.
    run2 = host.tick(at(1))
    assert [r.ok for r in run2.admitted] == [True]
    briefing = host.canonical / ".supervisor" / "runtime" / "briefings"
    planning_task = run2.admitted[0].task_id
    assert "## Planning backlog" in (briefing / f"{planning_task}.md").read_text()

    # The planning card requests an investigation and an experiment, and stages a note.
    planner = host.claim()
    investigation_request = planner.request("Investigate last hits", "investigation")
    experiment_request = planner.request("Test waiting for range", "experimentation")
    planner.stage_note("Plan: last hits first")
    planner.finish()

    # Tick 3: merge + publish; the investigation is admitted; the experiment needs calibration.
    run3 = host.tick(at(2))
    assert [h.state for h in run3.handoffs] == ["published"]
    assert host.receipt(planning_request)["state"] == "published"
    assert host.receipt(investigation_request)["state"] == "admitted"
    assert host.receipt(experiment_request) is None  # transient: retried later
    [calibration] = [
        r for r in host.requests() if "[calibrate:" in r.get("spec", {}).get("why", "")
    ]
    assert f"[calibrate:{GAME_VERSION}]" in calibration["spec"]["why"]
    assert host.remote_head() == host.head()

    # Tick 4: the calibration card is admitted.
    host.tick(at(3))
    assert host.receipt(calibration["id"])["state"] == "admitted"

    # Two cards run at once: the investigation and the calibration.
    investigation = host.claim("investigation")
    calibrator = host.claim("maintenance")
    hypothesis = investigation.hypothesis()
    investigation.stage_note("Found a lead on last hits")
    noise = {"game_version": GAME_VERSION, "computed_at": at(3).isoformat(), "evidence": [],
             "metrics": {}}  # fmt: skip
    (calibrator.worktree / "stats").mkdir(exist_ok=True)
    (calibrator.worktree / "stats" / "metric-noise.yaml").write_text(yaml.safe_dump(noise))
    investigation.finish()
    calibrator.finish()

    # Tick 5: both merge and publish; with noise current, the experiment is admitted.
    run5 = host.tick(at(4))
    assert sorted(h.state for h in run5.handoffs) == ["published", "published"]
    assert host.receipt(experiment_request)["state"] == "admitted"
    assert host.store.find("hypothesis") == [hypothesis]

    # The experiment card builds, uploads and launches its paired XP evaluation.
    experimenter = host.claim("experimentation")
    experiment = evaluating_experiment(experimenter.store, hypothesis)
    handles = launch_all(host.sm, experimenter.store, experiment)
    experimenter.stage_note("Launched the waiting-for-range evaluation")
    experimenter.finish()

    # Tick 6, two hours later: the experiment merges, and planning is due again.
    run6 = host.tick(at(120))
    assert run6.planning is not None and run6.planning.request_id != planning_request
    assert run6.failures == {}
    assert host.store.load(experiment)[0]["status"] == "evaluating"

    # xp-poll: the XP requests complete; evidence is committed and a review is requested.
    poll = xp_poll.run(host.ctx, host.sm, host.game, at(121), env=host.env)
    assert sorted(poll.evidence) == sorted(h.request_id for h in handles)
    [review] = poll.reviews
    review_key = review_marker(host.store.load(experiment)[0])
    assert f"[{review_key}]" in review.why

    # Tick 7: the review card (and the new planning card) are admitted.
    run7 = host.tick(at(122))
    assert run7.failures == {}
    assert host.receipt(review.request_id)["state"] == "admitted"
    assert host.receipt(run6.planning.request_id)["state"] == "admitted"

    # Canonical log, outbox and ledger.
    assert _subsequence(
        [
            r"^bootstrap: seed workspace from games/fakegame/seed$",
            r"^board epoch e1: board created by bootstrap$",
            rf"^runtime request {planning_request} \[planning:",
            rf"^admit {planning_request}$",
            rf"^merge card/{planning_request} ",
            r"^publish 1 message",
            rf"^admit {investigation_request}$",
            rf"^runtime request {calibration['id']} \[calibrate:{GAME_VERSION}\]$",
            rf"^admit {calibration['id']}$",
            rf"^admit {experiment_request}$",
            rf"^merge card/{experiment_request} ",
            rf"^runtime request {run6.planning.request_id} \[planning:",
            rf"^xp-poll: evidence EV-\w+ for {handles[0].request_id} ",
            rf"^runtime request {review.request_id} \[{re.escape(review_key)}\]$",
            rf"^admit {review.request_id}$",
        ],
        host.subjects(),
    ), host.subjects()
    outbox = [(m["seq"], m["kind"], m["from"], m["title"]) for m in host.outbox()]
    assert outbox == [
        (1, "note", "research-agent", "Plan: last hits first"),
        (2, "note", "research-agent", "Found a lead on last hits"),
        (3, "note", "research-agent", "Launched the waiting-for-range evaluation"),
    ]
    states = {
        rid: host.receipt(rid)["state"]
        for rid in (planning_request, investigation_request, calibration["id"], experiment_request)
    }
    assert states == {
        planning_request: "published",
        investigation_request: "published",
        calibration["id"]: "published",
        experiment_request: "published",
    }
    evidence = sorted((host.canonical / "evidence" / "index").glob("EV-*.json"))
    assert len(evidence) == len(handles)
    assert host.committed(host.ledger / "board.json")["epoch"] == "e1"
    assert (host.canonical / "kb" / "index.md").exists()  # generated views are committed
    assert host.remote_head() == host.head() and host.clean()


# --- containment, push, merge state ---


def test_a_failing_step_is_contained_and_becomes_a_signal(host, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("kb exploded")

    monkeypatch.setattr(queue_keeper, "kb_step", broken)

    result = host.tick(at(0))

    assert result.exit_code == 0 and result.failures == {"kb": "ValueError: kb exploded"}
    assert result.planning is not None  # later steps still ran
    assert any(s.code == KEEPER_STEP_FAILED and s.entity == "kb" for s in result.signals)
    log = (host.canonical / ".supervisor" / "runtime" / "keeper.log").read_text()
    assert "step kb failed: ValueError: kb exploded" in log


def test_a_raising_preflight_pauses_and_asks_for_help(host, monkeypatch):
    host.tick(at(0))

    def broken(*args, **kwargs):
        raise ValueError("limit_remaining is not a number")

    monkeypatch.setattr(queue_keeper, "preflight", broken)
    result = host.tick(at(1))

    assert result.paused and not result.preflight.ok
    assert result.preflight.reasons == [
        "preflight raised: ValueError: limit_remaining is not a number"
    ]
    assert result.admitted == []  # nothing is admitted while paused
    assert len(_help_requests(host, "Agent paused")) == 1


# --- champion conflict (A9) ---


def test_a_champion_conflict_pauses_tells_the_user_once_and_waits_for_agreement(
    host, monkeypatch
):
    host.tick(at(0))
    conflict = promotion.ChampionConflict(
        "ar-E-01M4EJ9RQMHVH6XK3N4T760MNG:v1", "hand-made:v2", "submitted by hand"
    )
    monkeypatch.setattr(promotion, "champion_conflict", lambda *args: conflict)

    first = host.tick(at(1))
    assert first.champion_conflict == conflict and first.resumed is None
    assert "champion conflict" in host.committed(host.ledger / "pause.json")["reason"]
    [message] = _help_requests(host, "unexpected champion")
    assert message["about"]["league_policy"] == "hand-made:v2"
    assert message["priority"] == "P0"
    assert first.failures == {}  # the rest of the run went on

    second = host.tick(at(2))  # still in conflict: still paused, no second message
    assert second.resumed is None and (host.ledger / "pause.json").exists()
    assert len(_help_requests(host, "unexpected champion")) == 1

    def league_unreadable(*args):
        raise RuntimeError("league read failed")

    monkeypatch.setattr(promotion, "current_champion", league_unreadable)
    third = host.tick(at(3))  # an unfinished check never resumes
    assert "champion" in third.failures and (host.ledger / "pause.json").exists()

    monkeypatch.undo()
    fourth = host.tick(at(4))  # the league and the loop agree again
    assert fourth.champion_checked and fourth.champion_conflict is None
    assert not (host.ledger / "pause.json").exists()
    resumed = [m for m in host.outbox() if m["title"] == "Agent resumed"][-1]
    assert "agree on the champion" in resumed["body"]


def test_a_rejected_submission_is_told_once_and_does_not_pause(host, monkeypatch):
    host.tick(at(0))
    basis = types.SimpleNamespace(
        id="P-01M4EJ9RQMHVH6XK3N4T760MNG",
        platform_policy_version_id="ar-E-01M4EJ9RQMHVH6XK3N4T760MNG:v1",
        replaces="fake-house:v1",
    )
    rejected = SubmissionState("rejected", detail="membership disqualified (inactive)")
    check = promotion.LeagueCheck("fake-house:v1", basis, rejected, None)
    monkeypatch.setattr(promotion, "league_check", lambda *args, **kwargs: check)
    first = host.tick(at(1))
    second = host.tick(at(2))
    assert first.champion_conflict is None and second.champion_checked
    assert not (host.ledger / "pause.json").exists()
    notes = [m for m in host.outbox() if m["title"] == "The league did not accept a submitted policy"]
    assert len(notes) == 1 and notes[0]["kind"] == "note"
    assert notes[0]["about"]["league_status"] == "membership disqualified (inactive)"


def test_grace_minutes_must_be_positive():
    assert promotion.champion_grace({}) == promotion.DEFAULT_CHAMPION_GRACE
    assert promotion.champion_grace({promotion.GRACE_ENV: "5"}) == datetime.timedelta(minutes=5)
    with pytest.raises(ValueError, match="positive"):
        promotion.champion_grace({promotion.GRACE_ENV: "0"})


def test_a_step_failing_every_run_escalates_once_per_episode(host, monkeypatch):
    state = {"broken": True}

    def kb(*args, **kwargs):
        if state["broken"]:
            raise ValueError("kb exploded")

    monkeypatch.setattr(queue_keeper, "kb_step", kb)
    for minute in range(4):
        assert host.tick(at(minute)).escalated == []
    assert host.tick(at(4)).escalated == ["kb"]  # the 5th consecutive failing run
    assert host.tick(at(5)).escalated == []
    [help_request] = _help_requests(host, "step kb failing")
    assert "kb exploded" in help_request["body"]

    state["broken"] = False
    host.tick(at(6))  # the episode ends
    state["broken"] = True
    for minute in range(7, 12):
        host.tick(at(minute))
    assert len(_help_requests(host, "step kb failing")) == 2


def test_a_step_failing_for_over_an_hour_escalates(host, monkeypatch):
    def kb(*args, **kwargs):
        raise ValueError("kb exploded")

    monkeypatch.setattr(queue_keeper, "kb_step", kb)
    assert host.tick(at(0)).escalated == []
    assert host.tick(at(61)).escalated == ["kb"]  # two runs, but failing for more than 1 h


def test_repeated_bootstrap_failure_escalates(host, monkeypatch):
    host.tick(at(0))  # canonical exists, so the help_request can be published
    failed = queue_keeper.BootstrapResult(ok=False, error="BootstrapError: remote unreadable")
    monkeypatch.setattr(queue_keeper, "bootstrap", lambda *args, **kwargs: failed)
    results = [host.tick(at(minute)) for minute in range(1, 7)]
    assert all(result.exit_code == 1 for result in results)
    assert [result.escalated for result in results].count(["bootstrap"]) == 1
    assert len(_help_requests(host, "step bootstrap failing")) == 1


def test_stale_merge_head_is_aborted(host):
    host.tick(at(0))
    canonical = host.canonical
    gitops.git(canonical, "checkout", "-q", "-b", "side")
    (canonical / "plan.md").write_text("side\n")
    gitops.commit_all(canonical, "side")
    gitops.git(canonical, "checkout", "-q", "main")
    (canonical / "plan.md").write_text("main\n")
    gitops.commit_all(canonical, "main change")
    with pytest.raises(gitops.GitError):
        gitops.git(canonical, "merge", "side")  # conflict: MERGE_HEAD left behind
    assert (canonical / ".git" / "MERGE_HEAD").exists()

    result = host.tick(at(1))

    assert not (canonical / ".git" / "MERGE_HEAD").exists()
    assert result.failures == {} and host.clean()
    assert host.remote_head() == host.head()


def test_crash_after_merge_fault_exits_before_publish_then_publishes_once(host, monkeypatch):
    """``AUTORESEARCH_FAKE_FAULTS=crash_after=merge`` kills the run between the merge commit
    and publication (the protocol slice's fault, Plan 01 Task 5.1); the next run publishes
    the merged handoff's message exactly once."""
    host.tick(at(0))
    host.tick(at(1))  # admits the planning request
    planner = host.claim()
    planner.stage_note("Plan: crash test")
    record = planner.finish()
    handoff = host.ledger / "handoffs" / record["card"] / f"{record['run_id']}.json"
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(queue_keeper.os, "_exit", fake_exit)
    host.env["AUTORESEARCH_FAKE_FAULTS"] = "crash_after=merge"
    with pytest.raises(SystemExit):
        host.tick(at(2))
    assert exits == [queue_keeper.CRASH_EXIT]
    assert host.committed(handoff)["state"] == "merged"
    assert host.outbox() == []

    host.env["AUTORESEARCH_FAKE_FAULTS"] = ""
    run = host.tick(at(3))
    assert run.failures == {}, run.failures
    assert host.committed(handoff)["state"] == "published"
    assert [(m["seq"], m["title"]) for m in host.outbox()] == [(1, "Plan: crash test")]
    host.tick(at(4))
    assert len(host.outbox()) == 1


def test_three_failed_pushes_raise_one_help_request(host):
    host.tick(at(0))
    gitops.git(host.canonical, "remote", "set-url", "origin", str(host.tmp_path / "gone.git"))

    results = [host.tick(at(minute)) for minute in range(1, 6)]

    assert [r.pushed for r in results] == [False] * 5
    assert len(_help_requests(host, "Push to origin failing")) == 1
    state = read_json(host.canonical / ".supervisor" / "runtime" / "push_state.json")
    assert state["consecutive_failures"] == 5

    gitops.git(host.canonical, "remote", "set-url", "origin", str(host.origin))
    assert host.tick(at(6)).pushed is True
    assert host.remote_head() == host.head()
    state = read_json(host.canonical / ".supervisor" / "runtime" / "push_state.json")
    assert state["consecutive_failures"] == 0 and state["help_sent"] is False


def test_bootstrap_failure_exits_1(tmp_path):
    host = Host(tmp_path, remote=False)
    result = host.tick(at(0))
    assert result.exit_code == 1 and not result.bootstrap.ok


def test_bootstrap_only_stops_after_bootstrap(host):
    result = host.tick(at(0), bootstrap_only=True)
    assert result.bootstrap.ok and result.preflight is None and result.planning is None
    assert host.requests() == [] and host.queue.board_exists()


def test_a_second_concurrent_run_is_skipped(host):
    from autoresearch.io.locks import file_lock

    lock = host.canonical / ".supervisor" / "runtime" / queue_keeper.RUN_LOCK
    with file_lock(lock):
        result = host.tick(at(0))
    assert result.skipped and result.bootstrap is None


# --- admission details ---


def _spec(title: str, mode: str = "investigation") -> CardSpec:
    return CardSpec("", title, mode, "Runtime test request.", [], "runtime", "P2")


def test_reprioritize_and_drop_requests(host):
    host.tick(at(0))
    keep = host.runtime_request(spec=_spec("Keep"))
    lose = host.runtime_request(spec=_spec("Lose"))
    host.tick(at(1))
    keep_task = host.receipt(keep)["tasks"][-1]["task_id"]
    lose_task = host.receipt(lose)["tasks"][-1]["task_id"]

    bump = host.runtime_request("reprioritize", target_request=keep, priority="P0")
    drop = host.runtime_request("drop", target_request=lose, reason="no longer needed")
    result = host.tick(at(2))

    assert {(o.op, o.state) for o in result.ops} == {("reprioritize", "applied"),
                                                     ("drop", "applied")}  # fmt: skip
    assert host.queue.get(keep_task).spec.priority == "P0"
    dropped = host.queue.get(lose_task)
    assert dropped.state == "completed" and dropped.runs[-1].outcome == "dropped"
    assert host.receipt(lose)["state"] == "dropped"
    for request_id in (bump, drop):
        assert host.committed(host.ledger / "ops" / f"{request_id}.json")["state"] == "applied"
    assert host.tick(at(3)).ops == []  # applied once
    assert host.queue.list("completed") == [dropped]  # no orphan repair for a dropped card


def test_a_second_planning_request_joins_the_backlog(host):
    host.tick(at(0))  # the cadence planning request
    extra = host.runtime_request(spec=_spec("Plan the jungle", "planning"))

    host.tick(at(1))

    assert host.receipt(extra)["state"] == "rejected"
    entry = host.committed(host.ledger / "backlog" / f"{extra}.json")
    assert entry["source"] == "request" and "Plan the jungle" in entry["summary"]
    [planning] = [c for c in host.queue.list() if c.spec.mode == "planning"]
    briefing = host.canonical / ".supervisor" / "runtime" / "briefings" / f"{planning.task_id}.md"
    host.tick(at(2))  # briefings are rewritten each run with the current backlog
    assert "Plan the jungle" in briefing.read_text()


# --- skills, proposals, skipped staged messages ---


def _stage_skill(host: Host, name: str, text: str) -> None:
    skill = host.home / "local" / "skills-staging" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(text)


def _suggestions(host: Host) -> list[dict]:
    return [m for m in host.outbox() if m["kind"] == "suggestion"]


def test_a_skill_proposed_before_runtime_key_existed_is_not_proposed_again(host):
    """Upgrade: a suggestion titled the old way (``Skill proposal: <name> (<hash>)``, no
    runtime_key) still counts, and a later change supersedes it."""
    host.tick(at(0))
    _stage_skill(host, "farm-faster", "# Farm faster\n")
    staged = proposals._files(host.home / "local" / "skills-staging" / "farm-faster")
    with canonical_lock(host.canonical):
        publish_runtime(
            host.canonical,
            "suggestion",
            f"Skill proposal: farm-faster ({proposals._tree_hash(staged)})",
            "Reply adopt, reject, or revise.",
            [{"uri": "ar://skill-proposal/farm-faster", "rev": host.head()}],
            allow_no_remote=True,
            expects_response=True,
        )

    assert host.tick(at(1)).skills == []
    [legacy] = _suggestions(host)

    _stage_skill(host, "farm-faster", "# Farm faster v2\n")
    assert host.tick(at(2)).skills == ["farm-faster"]
    assert _suggestions(host)[-1]["supersedes"] == legacy["id"]


def test_staged_skills_are_proposed_once_and_reraised_daily(host):
    host.tick(at(0))
    _stage_skill(host, "farm-faster", "# Farm faster\n")
    (host.home / "local" / "skills-staging" / "not-a-skill").mkdir()

    first = host.tick(at(1))

    assert first.skills == ["farm-faster"]
    assert (host.canonical / "skills-proposed" / "farm-faster" / "SKILL.md").exists()
    [suggestion] = _suggestions(host)
    assert suggestion["refs"][0]["uri"] == "ar://skill-proposal/farm-faster"
    assert host.tick(at(2)).skills == []  # unchanged: no second suggestion

    _stage_skill(host, "farm-faster", "# Farm faster v2\n")
    host.tick(at(3))
    old, new = _suggestions(host)
    assert new["supersedes"] == old["id"]

    later = host.tick(at(3 + 25 * 60))
    assert later.reraised == [new["id"]]
    reraise = _suggestions(host)[-1]
    assert reraise["reraises"] == new["id"] and reraise["title"].startswith("Still open:")
    assert host.tick(at(4 + 25 * 60)).reraised == []  # once a day


def test_skipped_staged_messages_are_reported_once(host):
    host.tick(at(0))
    results = [PublishResult(skipped={"M-01K00000000000000000000000": ["kind: bad"]})]
    report_skipped(host.canonical, results, allow_no_remote=False)
    report_skipped(host.canonical, results, allow_no_remote=False)
    notes = [m for m in host.outbox() if m["title"].startswith("Not published")]
    assert len(notes) == 1 and "kind: bad" in notes[0]["body"]
    assert host.clean()


def test_blocked_card_is_requeued_once_then_repaired(host):
    host.tick(at(0))
    host.tick(at(1))
    [card] = host.queue.list("pending")

    def block():
        record_path = host.queue.directory / "cards" / f"{card.task_id}.json"
        record = read_json(record_path)
        record["state"] = "blocked"
        write_json(record_path, record)

    block()
    first = host.tick(at(2))
    assert [a.action for a in first.blocked] == ["requeued"]
    assert host.queue.get(card.task_id).state == "pending"

    block()
    second = host.tick(at(3))
    [action] = second.blocked
    assert action.action == "repaired" and action.repair_request
    assert len(_help_requests(host, card.task_id)) == 1
    assert host.tick(at(4)).blocked == []  # nothing more


def test_tool_fault_sequence_publishes_the_agents_help_request_and_keeps_the_card_blocked(host):
    host.tick(at(0))
    host.tick(at(1))
    worker = host.claim()
    worker.block_for_tool_fault("softmax build E-1", "FileNotFoundError: gate.py")

    run = host.tick(at(2))
    assert [a.action for a in run.blocked] == ["tool-fault"]
    assert host.queue.get(worker.task_id).state == "blocked"
    [agent_help] = _help_requests(host, "softmax build E-1 failed")  # merged via the handoff
    assert "FileNotFoundError" in agent_help["body"]
    [runtime_help] = _help_requests(host, "Blocked by a tool fault")
    assert runtime_help["about"]["card"] == worker.task_id
    assert [r for r in host.requests() if "repair-blocked" in r["spec"]["why"]] == []

    assert host.tick(at(3)).blocked == []  # nothing more
    assert len(_help_requests(host, "softmax build E-1 failed")) == 1
    assert len(_help_requests(host, "Blocked by a tool fault")) == 1
    assert host.clean()


def test_main_builds_the_run_from_the_environment(host, monkeypatch, capsys):
    import json

    from autoresearch.runtime import config_migrations

    for name, value in host.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AUTORESEARCH_QUEUE", "file")
    monkeypatch.setattr(config_migrations, "hermes_runner", lambda argv, home: None)

    assert queue_keeper.main(["--bootstrap-only"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["exit_code"] == 0 and summary["failures"] == {}
    assert (host.canonical / ".git").exists() and host.remote_head() == host.head()


# --- KB staleness on a game-version change (runtime-only KB maintenance) ---


def _claim(host: Host, checked: str, evidence: list | None = None) -> str:
    claim = host.store.create(
        "claim",
        {
            "kind": "mechanism",
            "status": "belief",
            "confidence": 0.7,
            "evidence": evidence or [],
            "revise_if": "A replay shows otherwise.",
            "relations": [],
            "topics": [],
            "valid_from_version": checked,
            "last_checked_version": checked,
        },
        "Damage is 10.",
    )
    gitops.commit_all(host.canonical, f"claim {claim}")
    return claim


def _set_game_version(host: Host, version: str) -> None:
    path = host.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = read_json(path) if path.exists() else host.sm._initial_state()
    write_json(path, {**state, "game_version": version})


def test_kb_step_marks_claims_stale_when_the_game_version_changes(host):
    host.tick(at(0))
    current = _claim(host, GAME_VERSION)
    host.tick(at(1))
    assert host.store.load(current)[0]["status"] == "belief"  # same version: untouched

    _set_game_version(host, "fake-2")
    result = host.tick(at(2))

    assert "kb" not in result.failures
    meta, _ = host.store.load(current)
    assert meta["status"] == "stale" and "fake-2" in meta["status_reason"]
    assert gitops.git(host.canonical, "status", "--porcelain", "kb") == ""  # committed
    logs = sorted((host.canonical / "kb" / "log").glob("*.md"))
    host.tick(at(3))
    assert sorted((host.canonical / "kb" / "log").glob("*.md")) == logs  # marked once


def test_whitespace_only_version_difference_marks_nothing(host):
    host.tick(at(0))
    claim = _claim(host, GAME_VERSION)
    _set_game_version(host, f" {GAME_VERSION}\n")
    result = host.tick(at(1))
    assert "kb" not in result.failures
    assert host.store.load(claim)[0]["status"] == "belief"


# --- code-citation renewal when the game source pin changes ---


def _source_repo(tmp_path):
    """A local game source repo with two commits; line 8 of engine.nim changes."""
    repo = tmp_path / "game-src"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "t")
    lines = [f"line {n}" for n in range(1, 11)]
    (repo / "engine.nim").write_text("\n".join(lines) + "\n")
    old = gitops.commit_all(repo, "v1")
    lines[7] = "line 8 changed"
    (repo / "engine.nim").write_text("\n".join(lines) + "\n")
    new = gitops.commit_all(repo, "v2")
    return repo, old, new


def _cites(path_range: str, sha: str) -> list[dict]:
    return [{"ref": f"{path_range}@{sha}", "tier": "S", "direct": True, "stance": "supports"}]


def test_game_source_pin_change_renews_code_citations(host, tmp_path):
    repo, old, new = _source_repo(tmp_path)
    host.game = dataclasses.replace(
        host.game, source={"repo": str(repo), "pin": "coworld_manifest"}
    )
    host.tick(at(0))
    unchanged = _claim(host, GAME_VERSION, _cites("engine.nim:1-3", old))
    changed = _claim(host, GAME_VERSION, _cites("engine.nim:8", old))
    _set_fake(host, game_source_pin=old)
    host.tick(at(1))  # the first pin is only recorded
    ledger = host.canonical / ".supervisor" / "ledger" / "game_source.json"
    assert read_json(ledger)["pin"] == old
    assert host.store.load(unchanged)[0]["last_checked_version"] == GAME_VERSION

    _set_fake(host, game_source_pin=new, game_version="fake-2")
    result = host.tick(at(2))

    assert "game_source" not in result.failures and "kb" not in result.failures
    renewed, _ = host.store.load(unchanged)
    assert renewed["status"] == "belief" and renewed["last_checked_version"] == "fake-2"
    stale, _ = host.store.load(changed)
    assert stale["status"] == "stale"
    reverify = [
        r for r in host.requests() if f"ar://claim/{changed}" in r.get("spec", {}).get("refs", [])
    ]
    assert len(reverify) == 1 and f"reverify {old}..{new}" in reverify[0]["spec"]["why"]
    assert read_json(ledger)["pin"] == new
    assert gitops.git(host.canonical, "status", "--porcelain") == ""  # all committed
    checkout = host.home / "local" / "game-source" / "fakegame"
    assert (checkout / ".git").exists()


def test_without_a_source_field_the_pin_is_ignored(host, tmp_path):
    _set_fake(host, game_source_pin="0" * 40)
    host.tick(at(0))
    assert not (host.canonical / ".supervisor" / "ledger" / "game_source.json").exists()


def test_unreachable_source_keeps_the_old_pin_and_retries(host, tmp_path):
    host.game = dataclasses.replace(
        host.game, source={"repo": str(tmp_path / "missing"), "pin": "coworld_manifest"}
    )
    host.tick(at(0))
    _set_fake(host, game_source_pin="a" * 40)
    host.tick(at(1))
    _set_fake(host, game_source_pin="b" * 40)
    result = host.tick(at(2))
    assert "game_source" in result.failures and "kb" not in result.failures
    ledger = host.canonical / ".supervisor" / "ledger" / "game_source.json"
    assert read_json(ledger)["pin"] == "a" * 40


def _set_fake(host: Host, **values) -> None:
    path = host.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = read_json(path) if path.exists() else host.sm._initial_state()
    write_json(path, {**state, **values})


def test_a_renewal_failure_keeps_the_pin_and_still_runs_the_kb_step(host, tmp_path, monkeypatch):
    from autoresearch.paths import Layout
    from autoresearch.runtime.game_source import PinUpdate

    host.tick(at(0))
    claim = _claim(host, GAME_VERSION)
    ledger = host.canonical / ".supervisor" / "ledger" / "game_source.json"
    write_json(ledger, {"pin": "a" * 40, "repo": "r", "updated_at": "t"})
    gitops.commit_all(host.canonical, "pin a")
    layout = Layout(host.canonical)
    partial = layout.card_requests / "Q-partial.json"

    def broken(layout_, checkout, old, new, *, current_version, now):
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("{}\n")  # a request written before the failure
        raise ValueError("diff exploded")

    monkeypatch.setattr(queue_keeper, "renew_code_citations", broken)
    pin = PinUpdate("b" * 40, "a" * 40, "r", tmp_path)
    with pytest.raises(RuntimeError, match="renewal failed"):
        queue_keeper.kb_step(host.canonical, at(1), "fake-2", pin)

    assert read_json(ledger)["pin"] == "a" * 40  # unrecorded: the next run retries
    assert host.store.load(claim)[0]["status"] == "stale"  # stale marking still ran
    assert gitops.git(host.canonical, "status", "--porcelain") == ""  # incl. the request

    monkeypatch.setattr(queue_keeper, "renew_code_citations", lambda *a, **k: None)
    queue_keeper.kb_step(host.canonical, at(2), "fake-2", pin)
    assert read_json(ledger)["pin"] == "b" * 40

"""queue-keeper end to end (Plan 01 Task 3.9): the real ``queue_keeper.run`` over a
bootstrapped canonical with a bare ``origin``, a FileQueue board, FakeSoftmax and the
fakegame, with scripted card workers (``tests/support/keeper_host.py``)."""

import dataclasses
import datetime
import re
import types

import pytest
import yaml
from support.board import ForeignBoard
from support.keeper_host import GAME_VERSION, PROFILE, Host, evaluating_experiment, launch_all

from autoresearch.comms.inbound import InboundProblems, submit_inbound
from autoresearch.comms.publisher import PublishResult, publish_runtime
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.card import CardSpec
from autoresearch.runtime import promotion, proposals, provenance, queue_keeper, xp_poll
from autoresearch.runtime.foreign import report_foreign_cards
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
        kind="experiment",
    )
    rejected = SubmissionState("rejected", detail="membership disqualified (inactive)")
    check = promotion.LeagueCheck("fake-house:v1", basis, rejected, None)
    monkeypatch.setattr(promotion, "league_check", lambda *args, **kwargs: check)
    first = host.tick(at(1))
    second = host.tick(at(2))
    assert first.champion_conflict is None and second.champion_checked
    assert not (host.ledger / "pause.json").exists()
    notes = [
        m for m in host.outbox() if m["title"] == "The league did not accept a submitted policy"
    ]
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
    host.runtime_commit("main change")
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


def _diverge_remote_card_branch(host, card_branch):
    """Leave origin with a card branch the local one is not a fast-forward of (a retried card)."""
    gitops.git(host.canonical, "branch", card_branch, "HEAD")
    gitops.push(host.canonical, "origin", [f"refs/heads/{card_branch}"])
    gitops.git(host.canonical, "commit", "--allow-empty", "-m", "rebuilt card")
    gitops.git(host.canonical, "branch", "-f", card_branch, "HEAD")
    gitops.git(host.canonical, "reset", "--hard", "HEAD~1")


def test_a_rebuilt_card_branch_is_force_pushed_and_main_still_reaches_origin(host):
    host.tick(at(0))
    _diverge_remote_card_branch(host, "card/Q-01REBUILT")

    result = host.tick(at(1))

    assert result.pushed is True
    remote = gitops.git(host.origin, "rev-parse", "refs/heads/card/Q-01REBUILT")
    assert remote == gitops.git(host.canonical, "rev-parse", "refs/heads/card/Q-01REBUILT")
    log = (host.canonical / ".supervisor" / "runtime" / "keeper.log").read_text()
    assert "card branch was rejected" not in log


def test_a_rejected_card_ref_is_logged_once_and_never_hides_a_main_failure(host, monkeypatch):
    host.tick(at(0))
    real = gitops.push_with_rejections
    refused = "refs/heads/card/Q-01REFUSED"
    monkeypatch.setattr(
        queue_keeper.gitops,
        "push_with_rejections",
        lambda repo, remote, refspecs: (
            (False, [refused]) if refspecs[0].startswith("+") else real(repo, remote, refspecs)
        ),
    )

    for minute in (1, 2, 3):
        assert host.tick(at(minute)).pushed is True
    log = (host.canonical / ".supervisor" / "runtime" / "keeper.log").read_text()
    assert log.count(f"push: card branch rejected by origin: {refused}") == 1

    gitops.git(host.canonical, "remote", "set-url", "origin", str(host.tmp_path / "gone.git"))
    assert host.tick(at(4)).pushed is False


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


def test_a_failing_op_is_contained_retried_then_given_up_once(host, monkeypatch):
    """On the box Hermes refused every drop of one card (its parent was blocked), and the
    whole ops step failed every run. Now that op fails alone: the other ops apply, it is
    retried, and after 3 failing runs one help_request reports it and it stops."""
    host.tick(at(0))
    keep = host.runtime_request(spec=_spec("Keep"))
    stuck = host.runtime_request(spec=_spec("Stuck child"))
    host.tick(at(1))
    stuck_task = host.receipt(stuck)["tasks"][-1]["task_id"]

    def refuse(task_id, reason):
        raise RuntimeError(
            f"cannot complete {task_id}: unsatisfied parent dependencies: t_8001dac2 (blocked)"
        )

    monkeypatch.setattr(host.queue, "drop", refuse)
    drop = host.runtime_request("drop", target_request=stuck, reason="stale")
    bump = host.runtime_request("reprioritize", target_request=keep, priority="P0")

    first = host.tick(at(2))
    assert {o.request_id: o.state for o in first.ops} == {drop: "retrying", bump: "applied"}
    assert host.tick(at(3)).ops[0].state == "retrying"  # the bump is applied once
    assert "ops" not in first.failures and "admission" not in first.failures
    assert host.committed(host.ledger / "ops" / f"{drop}.json") is None
    assert _help_requests(host, drop) == []

    result = host.tick(at(4))
    assert [(o.request_id, o.state) for o in result.ops] == [(drop, "failed")]
    marker = host.committed(host.ledger / "ops" / f"{drop}.json")
    assert marker["state"] == "failed" and "unsatisfied parent" in marker["detail"]
    [help_request] = _help_requests(host, drop)
    assert help_request["about"]["card"] == stuck_task
    assert "archive" in help_request["body"]
    assert host.receipt(stuck)["state"] == "admitted"  # nothing was dropped

    assert host.tick(at(5)).ops == []  # given up for good
    assert len(_help_requests(host, drop)) == 1


def test_a_give_up_whose_marker_commit_fails_is_not_reported_twice(host, monkeypatch):
    from autoresearch.runtime import queue_ops

    host.tick(at(0))
    stuck = host.runtime_request(spec=_spec("Stuck child"))
    host.tick(at(1))

    def refuse(task_id, reason):
        raise RuntimeError("cannot complete: unsatisfied parent dependencies")

    monkeypatch.setattr(host.queue, "drop", refuse)
    real_record = queue_ops._record
    failed_records = []

    def lose_first_failed_marker(canonical, request, state, detail):
        if state == "failed" and not failed_records:
            failed_records.append(state)
            raise RuntimeError("commit failed")
        real_record(canonical, request, state, detail)

    monkeypatch.setattr(queue_ops, "_record", lose_first_failed_marker)
    drop = host.runtime_request("drop", target_request=stuck, reason="stale")
    for minute in (2, 3, 4):
        host.tick(at(minute))
    assert host.committed(host.ledger / "ops" / f"{drop}.json") is None  # its commit failed
    assert len(_help_requests(host, drop)) == 1  # published before the marker

    assert host.tick(at(5)).ops[0].state == "failed"
    assert host.committed(host.ledger / "ops" / f"{drop}.json")["state"] == "failed"
    assert len(_help_requests(host, drop)) == 1  # not published again


def test_a_given_up_drop_is_recorded_once_a_human_archives_the_card(host, monkeypatch):
    """The give-up help_request asks a human to archive the card; the runtime then records
    the drop (receipt dropped), and the archived card is never rebuilt or re-admitted."""
    from support.archiving_queue import ArchivingQueue

    host.queue = ArchivingQueue(host.queue)
    host.tick(at(0))
    stuck = host.runtime_request(spec=_spec("Stuck child"))
    host.tick(at(1))
    stuck_task = host.receipt(stuck)["tasks"][-1]["task_id"]

    def refuse(task_id, reason):
        raise RuntimeError("cannot complete: unsatisfied parent dependencies")

    monkeypatch.setattr(host.queue, "drop", refuse)
    drop = host.runtime_request("drop", target_request=stuck, reason="stale")
    for minute in (2, 3, 4):
        host.tick(at(minute))
    assert host.committed(host.ledger / "ops" / f"{drop}.json")["state"] == "failed"
    assert host.tick(at(5)).ops == []  # still on the board: nothing to record

    host.queue.archive(stuck_task)  # the human follows the help_request
    result = host.tick(at(6))

    assert [(o.request_id, o.state) for o in result.ops] == [(drop, "applied")]
    assert host.committed(host.ledger / "ops" / f"{drop}.json")["state"] == "applied"
    assert host.receipt(stuck)["state"] == "dropped"
    assert result.rebuilt == []
    assert len(host.receipt(stuck)["tasks"]) == 1  # never re-admitted
    assert host.tick(at(7)).ops == []


def test_a_crash_between_the_archive_and_its_commit_settles_without_a_rebuild(host, monkeypatch):
    from support.archiving_queue import ArchivingQueue

    from autoresearch.runtime import queue_ops

    host.queue = ArchivingQueue(host.queue)
    host.tick(at(0))
    lose = host.runtime_request(spec=_spec("Lose"))
    host.tick(at(1))
    lose_task = host.receipt(lose)["tasks"][-1]["task_id"]
    real_record = queue_ops._record
    calls = []

    def crash_once(canonical, request, state, detail):
        calls.append(state)
        if len(calls) == 1:
            raise RuntimeError("killed before the commit")
        real_record(canonical, request, state, detail)

    monkeypatch.setattr(queue_ops, "_record", crash_once)
    drop = host.runtime_request("drop", target_request=lose, reason="no longer needed")
    first = host.tick(at(2))
    assert lose_task in host.queue.archived  # the archive happened, its commit did not
    assert first.ops[0].state == "retrying"
    assert host.receipt(lose)["state"] == "admitted"

    second = host.tick(at(3))
    assert second.rebuilt == []
    assert [(o.request_id, o.state) for o in second.ops] == [(drop, "applied")]
    assert host.receipt(lose)["state"] == "dropped"
    assert len(host.receipt(lose)["tasks"]) == 1


def test_missing_host_requirements_get_one_help_request_and_no_pause(host):
    """On the box GotA build cards each failed fast for want of GOTA_* and Nim; queue-keeper
    now says so once instead of every card discovering it."""
    host.game = dataclasses.replace(
        host.game, host_env=("GAME_BUILD_PYTHON",), host_tools=("no-such-tool-xyz",)
    )
    host.tick(at(0))
    result = host.tick(at(1))
    assert len(result.host_problems) == 2
    assert not result.paused and "host" not in result.failures
    [help_request] = _help_requests(host, "Host setup missing")
    assert "GAME_BUILD_PYTHON" in help_request["body"] and "no-such-tool-xyz" in help_request["body"]
    host.tick(at(2))
    assert len(_help_requests(host, "Host setup missing")) == 1  # once per distinct set

    with (host.home / ".env").open("a") as env_file:
        env_file.write("GAME_BUILD_PYTHON=/usr/bin/env\n")
    assert host.tick(at(3)).host_problems == ["no-such-tool-xyz is not on the workers' PATH"]
    assert len(_help_requests(host, "Host setup missing")) == 2  # a new set is reported


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


def test_foreign_cards_are_reported_once_and_never_merged_while_others_merge(host):
    """Two cards the runtime did not create: one that just runs, and one whose worker forged
    a receipt and wrote a handoff past card finish (the box, 2026-10-09). Each gets one marker
    and one help_request; the forged work is not merged; the admitted card's handoff merges
    in the same run and no step fails."""
    host.tick(at(0))
    host.runtime_request(spec=_spec("Admitted"))
    host.tick(at(1))
    forged = host.foreign_card("Hand-made design", forged_receipt=True)
    running = host.foreign_card("Hand-made run")
    normal = host.claim(mode="investigation")
    forged_record = forged.finish(forged=True)
    normal_record = normal.finish()
    host.queue = ForeignBoard(host.queue, forged.task_id, running.task_id, title="Hand-made")

    # First protection: the forged receipt was committed on canonical outside the runtime,
    # so the provenance check pauses before anything merges (C1).
    stopped = host.tick(at(2))
    violation = stopped.provenance_violation
    assert violation.kind == provenance.UNKNOWN_COMMITS and stopped.handoffs == []
    [forged_commit] = violation.commits
    assert forged_commit.subject.startswith("admit ")
    assert len([m for m in host.outbox() if m.get("runtime_key", "").startswith("provenance")]) == 1
    with canonical_lock(host.canonical):  # what the operator's runtime accept-commits does
        provenance.accept(host.canonical, forged_commit.sha)

    # Second protection: once accepted, the foreign cards are still never merged.
    run = host.tick(at(2.5))

    assert {"foreign", "handoffs"}.isdisjoint(run.failures)
    assert sorted(run.foreign) == sorted([forged.task_id, running.task_id])
    assert [(o.task_id, o.state) for o in run.handoffs] == [(normal.task_id, "published")]
    assert gitops.contains(host.canonical, normal_record["result_sha"])
    assert not gitops.contains(host.canonical, forged_record["result_sha"])
    for card in (forged, running):
        assert host.committed(host.ledger / "foreign" / f"{card.task_id}.json") is not None
        [help_request] = _help_requests(host, card.task_id)
        assert help_request["title"] == "Card not created by the runtime: Hand-made"
        assert ("operator" in help_request["body"]) == (card is forged)

    again = host.tick(at(3))
    assert {"foreign", "handoffs"}.isdisjoint(again.failures)
    assert again.foreign == [] and again.handoffs == []
    assert len(_help_requests(host, "Card not created by the runtime")) == 2
    assert host.clean()


def test_spend_caps_stop_a_foreign_card(host):
    host.tick(at(0))
    running = host.foreign_card("Hand-made run")
    host.queue.requeue(running.task_id, "a second run")
    host.queue.claim_next(f"worker-{running.task_id}")  # 2 runs, still running
    host.queue = ForeignBoard(host.queue, running.task_id, title="Hand-made")
    host.env["AUTORESEARCH_MAX_CARD_RUNS"] = "1"

    run = host.tick(at(1))

    assert [task_id for task_id, _ in run.spend_run.stopped] == [running.task_id]
    assert host.queue.queue.get(running.task_id).state == "blocked"


class _BrokenForeignCards(ForeignBoard):
    """Hermes cannot show one task, so ``foreign_cards`` raises."""

    def foreign_cards(self):
        raise RuntimeError("database is locked")


def test_spend_caps_still_stop_admitted_cards_when_foreign_cards_fail(host):
    host.tick(at(0))
    host.runtime_request(spec=_spec("Admitted"))
    host.tick(at(1))
    card = host.claim(mode="investigation")
    host.queue.requeue(card.task_id, "a second run")
    host.queue.claim_next(f"worker-{card.task_id}")  # 2 runs, still running
    host.queue = _BrokenForeignCards(host.queue)
    host.env["AUTORESEARCH_MAX_CARD_RUNS"] = "1"

    run = host.tick(at(2))

    assert [task_id for task_id, _ in run.spend_run.stopped] == [card.task_id]
    assert "database is locked" in run.spend_run.failures["foreign"]


def test_the_foreign_step_does_nothing_before_the_board_exists(host):
    class MissingBoard:
        def board_exists(self):
            return False

        def foreign_cards(self):
            raise AssertionError("the board is missing: nothing to list")

    assert report_foreign_cards(host.ctx, MissingBoard(), allow_no_remote=True) == []


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
    host.runtime_commit(f"claim {claim}")
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
    host.runtime_commit("pin a")
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


# --- start on request (AUTORESEARCH_AUTONOMY) ---


def _planning_requests(host: Host) -> list[dict]:
    return [r for r in host.requests() if r.get("spec", {}).get("mode") == "planning"]


def _started(host: Host) -> dict | None:
    return host.committed(host.ledger / "started.json")


def _send_task(host: Host, author: str, refs: list[str] | None = None) -> str:
    return submit_inbound(
        host.canonical,
        "task",
        {
            "title": "Scout andre",
            "mode": "scouting",
            "why": "Leads.",
            "refs": refs or [],
            "priority": "P1",
        },
        author,
    )


def test_on_request_does_not_plan_before_a_coach_task(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"

    first, second = host.tick(at(0)), host.tick(at(1))

    for run in (first, second):
        assert run.planning is None and run.planning_skipped and run.failures == {}
        assert run.admitted == []
    assert host.queue.list() == [] and _planning_requests(host) == []
    assert _started(host) is None
    log = (host.canonical / ".supervisor" / "runtime" / "keeper.log").read_text()
    assert log.count("planning waits for the coach's first task") == 1  # once a day


def test_first_coach_task_starts_the_agent(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    task = _send_task(host, "coach:ply_x")

    run = host.tick(at(1))

    assert [r.ok for r in run.admitted] == [True]
    assert _started(host)["by"] == task and _started(host)["started_at"]
    assert host.clean()
    host.tick(at(2))
    assert len(_planning_requests(host)) == 1  # the agent plans from now on


def test_a_rejected_coach_task_does_not_start_the_agent(host):
    """M1: a coach task routing answers with a rejection note (its refs do not resolve) does
    not start the agent; the next accepted coach task does."""
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    rejected = _send_task(host, "coach:ply_x", refs=["ar://claim/C-01K6Z3V8QX0000000000000009"])

    run = host.tick(at(1))

    record = host.committed(host.ledger / "inbound" / f"{rejected}.json")
    assert record["state"] == "acknowledged" and record["responsible"]["request_id"] is None
    assert _started(host) is None and run.planning_skipped and run.failures == {}
    task = _send_task(host, "coach:ply_x")
    host.tick(at(2))
    assert _started(host)["by"] == task


def test_coach_steer_does_not_start_the_agent(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    steer = submit_inbound(
        host.canonical, "steer", {"text": "Stop that task.", "urgency": "now"}, "coach:ply_x"
    )

    run = host.tick(at(1))

    assert host.committed(host.ledger / "inbound" / f"{steer}.json") is not None  # adopted
    assert _started(host) is None and run.planning_skipped


def test_non_coach_task_does_not_start_the_agent(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    _send_task(host, "operator")

    run = host.tick(at(1))

    assert [r.ok for r in run.admitted] == [True]
    assert _started(host) is None and run.planning_skipped


def test_always_is_unchanged(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "always"
    run = host.tick(at(0))
    assert run.planning is not None and not run.planning_skipped and run.failures == {}
    assert _started(host) is None


def test_unknown_autonomy_runs_as_always_with_one_help_request(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on_request"
    first = host.tick(at(0))
    host.tick(at(1))
    assert first.planning is not None
    assert "autonomy" in first.failures
    assert len(_help_requests(host, "Autonomy setting")) == 1


# --- coach pause and resume (inbound control, D15) ---


def _control(host: Host, action: str) -> str:
    return submit_inbound(host.canonical, "control", {"action": action}, "coach:ply_x")


def _replies(host: Host, inbound_id: str) -> list[dict]:
    return [m for m in host.outbox() if m.get("in_reply_to") == inbound_id]


def test_a_coach_task_and_a_pause_in_one_run_start_the_agent_and_hold_the_task(host):
    """Routing takes the task first (controls come after the other kinds): the task is routed
    and starts the agent; then the pause holds research, so admission defers its request and
    planning waits. The resume admits it."""
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    control = _control(host, "pause")  # sent first: id order must not matter
    task = _send_task(host, "coach:ply_x")

    run = host.tick(at(1))

    record = host.committed(host.ledger / "inbound" / f"{task}.json")
    assert record["state"] == "routed" and record["responsible"]["request_id"]
    assert _started(host)["by"] == task
    assert (host.ledger / "coach-hold.json").exists()
    assert not any(result.ok for result in run.admitted)  # deferred: "paused"
    assert host.receipt(record["responsible"]["request_id"]) is None
    assert run.planning is None and run.planning_skipped and run.failures == {}
    [note] = _replies(host, control)
    assert note["title"] == "Research paused at your request."

    _control(host, "resume")
    resumed = host.tick(at(2))
    assert [r.ok for r in resumed.admitted] == [True]
    assert host.receipt(record["responsible"]["request_id"]) is not None


@pytest.mark.parametrize("fault", ["identity=rejected", "fail_after=whoami", "credit"])
def test_cancellation_runs_while_preflight_blocks_new_work(host, fault):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    original = _send_task(host, "coach:ply_x")
    host.tick(at(1))
    worker = host.claim("scouting")
    record = host.committed(host.ledger / "inbound" / f"{original}.json")
    request = record["responsible"]["request_id"]
    waiting = _send_task(host, "coach:ply_x")
    control = submit_inbound(
        host.canonical, "control", {"action": "cancel", "task": original}, "coach:ply_x"
    )
    tick_options = {}
    if fault == "credit":
        host.env["AUTORESEARCH_SKIP_OPENROUTER_CHECK"] = "0"
        host.env["OPENROUTER_API_KEY"] = "test-only-key"
        tick_options["http_get"] = lambda _url, _headers: '{"data":{"limit_remaining":0}}'
    else:
        host.set_faults(fault)

    run = host.tick(at(2), **tick_options)

    assert not run.preflight.ok and run.failures == {}
    assert host.queue.get(worker.task_id).state == "blocked"
    assert host.receipt(request)["state"] == "dropped"
    assert host.receipt(request)["cancelled_by"] == control
    assert host.committed(host.ledger / "inbound" / f"{waiting}.json")["state"] == "received"
    assert run.admitted == []
    assert len(_replies(host, control)) == 1

    host.tick(at(3), **tick_options)
    assert len(_replies(host, control)) == 1
    host.set_faults("")
    host.env["AUTORESEARCH_SKIP_OPENROUTER_CHECK"] = "1"
    host.tick(at(4))
    assert host.queue.get(worker.task_id).state == "blocked"
    assert host.receipt(request)["state"] == "dropped"


def test_cancel_before_first_routing_never_starts_research(host):
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    host.tick(at(0))
    original = _send_task(host, "coach:ply_x")
    control = submit_inbound(
        host.canonical, "control", {"action": "cancel", "task": original}, "coach:ply_x"
    )

    run = host.tick(at(1))

    assert run.failures == {} and run.admitted == []
    assert host.requests() == [] and host.queue.list() == []
    assert _started(host) is None
    assert host.committed(host.ledger / "inbound" / f"{original}.json")["state"] == "acknowledged"
    assert len(_replies(host, control)) == 1


def _held_card(host: Host) -> str:
    """Tick until a planning card is pending and assigned, then hold research; return it."""
    host.tick(at(0))
    host.tick(at(1))
    [card] = host.queue.list("pending")
    assert card.assignee == PROFILE
    _control(host, "pause")
    host.tick(at(2))
    return card.task_id


def test_a_control_pause_holds_research_while_running_cards_keep_running(host):
    host.tick(at(0))
    host.tick(at(1))
    running = host.claim()  # the planning card
    pending_request = host.runtime_request(spec=_spec("Pending"))
    host.tick(at(2))
    pending = host.receipt(pending_request)["tasks"][-1]["task_id"]
    assert host.queue.get(pending).assignee == PROFILE
    control = _control(host, "pause")
    waiting = host.runtime_request(spec=_spec("Waiting"))

    run = host.tick(at(3))

    hold = host.committed(host.ledger / "coach-hold.json")
    assert hold["by"] == "coach:ply_x" and hold["held_at"]
    assert hold["tasks"] == {pending: PROFILE}
    assert host.queue.get(pending).assignee is None  # never dispatched
    assert host.queue.get(running.task_id).state == "claimed"  # keeps running
    assert run.spend_run is not None  # ... under its caps
    assert host.receipt(waiting) is None  # admission stopped
    assert run.planning is None and run.planning_skipped and run.failures == {}
    [note] = _replies(host, control)
    assert note["from"] == "runtime" and note["kind"] == "note"
    assert note["title"] == "Research paused at your request."
    record = host.committed(host.ledger / "inbound" / f"{control}.json")
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    assert not (host.ledger / "pause.json").exists()  # independent of the C7 pause

    later = host.tick(at(60 * 24))  # a day later planning would be due: still held
    assert later.planning is None and later.planning_skipped
    assert host.receipt(waiting) is None and len(_replies(host, control)) == 1
    log = (host.canonical / ".supervisor" / "runtime" / "keeper.log").read_text()
    assert "planning waits: the coach paused research" in log


def test_queue_keeper_never_clears_a_control_hold(host):
    card = _held_card(host)

    assert not host.tick(at(3)).paused  # preflight passes
    assert (host.ledger / "coach-hold.json").exists()
    host.set_faults("player=ply_someone_else")
    assert host.tick(at(4)).paused
    host.set_faults("")
    run = host.tick(at(5))  # preflight passes again: the agent's own pause stays while held

    assert not run.paused and run.resumed is None and run.failures == {}
    assert (host.ledger / "coach-hold.json").exists() and (host.ledger / "pause.json").exists()
    assert host.queue.get(card).assignee is None
    assert not [m for m in host.outbox() if m["title"] == "Agent resumed"]


def test_a_control_resume_releases_the_hold_and_one_without_a_hold_says_so(host):
    card = _held_card(host)
    resume = _control(host, "resume")

    run = host.tick(at(3))

    assert not (host.ledger / "coach-hold.json").exists()
    assert host.committed(host.ledger / "coach-hold.json") is None
    assert host.queue.get(card).assignee == PROFILE
    [note] = _replies(host, resume)
    assert note["title"] == "Research resumed." and run.failures == {}
    assert not run.planning_skipped

    again = _control(host, "resume")
    host.tick(at(4))
    [note] = _replies(host, again)
    assert note["title"] == "Research was not paused"
    assert "Nothing was paused" in note["body"]


def test_a_control_resume_leaves_the_agents_own_pause_in_place(host, monkeypatch):
    card = _held_card(host)
    conflict = promotion.ChampionConflict("ar-E-x:v1", "hand-made:v2", "submitted by hand")
    monkeypatch.setattr(promotion, "champion_conflict", lambda *args: conflict)
    host.tick(at(3))  # the agent pauses itself too
    resume = _control(host, "resume")

    host.tick(at(4))

    assert not (host.ledger / "coach-hold.json").exists()
    pause = host.committed(host.ledger / "pause.json")
    assert "champion conflict" in pause["reason"] and pause["tasks"] == {card: PROFILE}
    assert host.queue.get(card).assignee is None  # still paused
    [note] = _replies(host, resume)
    assert note["title"] == "Research resumed." and "still paused" in note["body"]

    monkeypatch.undo()
    host.tick(at(5))  # the conflict clears: the agent resumes by itself
    assert not (host.ledger / "pause.json").exists()
    assert host.queue.get(card).assignee == PROFILE


def test_a_control_with_an_unknown_action_is_rejected_with_a_note(host):
    host.tick(at(0))
    inbound_id = "ply_x:control:bad"
    write_json(
        host.canonical / "comms" / "inbox" / f"{inbound_id}.json",
        {"id": inbound_id, "created": "2026-10-09T20:00:00Z", "author": "coach:ply_x",
         "kind": "control", "action": "stop"},
    )  # fmt: skip
    with pytest.raises(InboundProblems, match="action: must be one of pause, resume"):
        submit_inbound(host.canonical, "control", {"action": "stop"}, "coach:ply_x")

    run = host.tick(at(1))

    [note] = _replies(host, inbound_id)
    assert note["about"]["outcome"] == "rejected"
    assert "action: must be one of pause, resume" in note["body"]
    assert not (host.ledger / "coach-hold.json").exists() and run.failures == {}


def _block(host: Host, task_id: str) -> None:
    record_path = host.queue.directory / "cards" / f"{task_id}.json"
    record = read_json(record_path)
    record["state"] = "blocked"
    write_json(record_path, record)


def test_a_card_blocked_under_a_control_hold_is_not_requeued_until_resume(host):
    host.tick(at(0))
    host.tick(at(1))
    running = host.claim()  # keeps its assignee: the hold unassigns only pending cards
    _control(host, "pause")
    host.tick(at(2))
    _block(host, running.task_id)

    held = host.tick(at(3))

    assert held.blocked == [] and host.queue.get(running.task_id).state == "blocked"
    _control(host, "resume")
    resumed = host.tick(at(4))
    assert [a.action for a in resumed.blocked] == ["requeued"]
    assert host.queue.get(running.task_id).state == "pending"


def test_a_card_blocked_under_a_pause_is_not_requeued_until_resume(host, monkeypatch):
    host.tick(at(0))
    host.tick(at(1))
    running = host.claim()
    conflict = promotion.ChampionConflict("ar-E-x:v1", "hand-made:v2", "submitted by hand")
    monkeypatch.setattr(promotion, "champion_conflict", lambda *args: conflict)
    host.tick(at(2))  # paused
    _block(host, running.task_id)

    paused = host.tick(at(3))

    assert paused.blocked == [] and host.queue.get(running.task_id).state == "blocked"
    monkeypatch.undo()
    resumed = host.tick(at(4))  # resumes (step 12) after the blocked step: requeued next run
    assert resumed.blocked == [] and not (host.ledger / "pause.json").exists()
    assert [a.action for a in host.tick(at(5)).blocked] == ["requeued"]
    assert host.queue.get(running.task_id).state == "pending"

"""queue-keeper end to end (Plan 01 Task 3.9): the real ``queue_keeper.run`` over a
bootstrapped canonical with a bare ``origin``, a FileQueue board, FakeSoftmax and the
fakegame, with scripted card workers (``tests/support/keeper_host.py``)."""

import datetime
import re

import pytest
import yaml
from support.keeper_host import GAME_VERSION, PROFILE, Host, evaluating_experiment, launch_all

from autoresearch.comms.publisher import PublishResult
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.card import CardSpec
from autoresearch.runtime import queue_keeper, xp_poll
from autoresearch.runtime.markers import review_marker
from autoresearch.runtime.queue_keeper import report_skipped
from autoresearch.runtime.signals import KEEPER_STEP_FAILED

T0 = datetime.datetime.now(datetime.UTC).replace(minute=5, second=0, microsecond=0)


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    return Host(tmp_path)


def _help_requests(host: Host, text: str = "") -> list[dict]:
    return [m for m in host.outbox() if m["kind"] == "help_request" and text in m["title"]]


def _subsequence(patterns: list[str], subjects: list[str]) -> bool:
    """True if each pattern matches a subject, in order."""
    remaining = iter(subjects)
    return all(any(re.search(p, s) for s in remaining) for p in patterns)


# --- identity failure on the first tick (C6, C7) ---


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
    notes = [m for m in host.outbox() if "was not published" in m["title"]]
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


def _claim(host: Host, checked: str) -> str:
    claim = host.store.create(
        "claim",
        {
            "kind": "mechanism",
            "status": "belief",
            "confidence": 0.7,
            "evidence": [],
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

"""Champion submission: the orchestrator's guarded submit, write-ahead stages, recovery and conflicts.

Runs against a research board (a bare canonical repository and a clone, which here stands for
the orchestrator's) and the FakeSoftmax, whose league admits a submission at once.
"""

import json
import os
import subprocess
import sys

import pytest
from support.research_board import BASELINE_PV, LEAGUE, PLAYER, PV_A, ResearchBoard, fake_state

from ide_research_core import champion_submission
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_json
from ide_research_core.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
from ide_research_core.softmax_tool.protocol import Refused


@pytest.fixture
def board(tmp_path):
    return ResearchBoard(tmp_path)


def published_on_canonical(board) -> list[str]:
    files = gitops.git(board.origin, "ls-tree", "-r", "--name-only", "main", "champion-submissions/")
    return [json.loads(gitops.git(board.origin, "show", f"main:{f}"))["stage"] for f in files.split()]


def test_submit_records_intent_then_submitted_and_status_publishes_the_champion(board):
    experiment, version = board.reviewed_experiment()

    result = champion_submission.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)

    assert result["stage"] == "submitted" and result["submission_id"]
    stages = [entry["stage"] for entry in result["history"]]
    assert stages == ["intent", "submitted"]  # intent was published before the platform call
    assert board.store.find("version_event", version=version, kind="submitted")
    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert report["in_flight"] is None and report["conflict"] is None
    assert report["champion"]["platform_policy_version_id"] == PV_A
    champion = gitops.git(board.origin, "show", f"main:{champion_submission.CHAMPION_PATH}")
    assert champion == f"# Candidate for {experiment}"
    assert "published" in published_on_canonical(board)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"decision": "hold"}, "not_submit"),
        ({"outcome": {"submit_eligible": False, "failures": ["ci"]}}, "not_eligible"),
        ({"outcome": {"rows_sha256": "0" * 64}}, "stale_outcome"),
        ({"outcome": {"champion": "someone-else:v1"}}, "baseline_not_champion"),
    ],
)
def test_guards_refuse_before_anything_is_recorded(board, kwargs, reason):
    experiment, _ = board.reviewed_experiment(**kwargs)
    with pytest.raises(Refused) as refused:
        champion_submission.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == reason
    assert champion_submission.in_flight(board.canonical) is None
    assert board.sm.submissions(PLAYER)[-1]["policy_version_id"] == BASELINE_PV


def test_a_wrong_identity_is_refused(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "player=ply_other"})
    with pytest.raises(Refused) as refused:
        champion_submission.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == "identity"


def test_one_submission_at_a_time(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        champion_submission.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    second, _ = board.reviewed_experiment()
    with pytest.raises(Refused) as refused:
        champion_submission.submit(board.canonical, board.sm, second, LEAGUE, PLAYER)
    assert refused.value.reason == "submission_in_flight"


def test_a_permit_ask_waits_at_intent_and_status_sends_after_the_yes(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused) as refused:
        champion_submission.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    assert refused.value.reason == "permit_required"
    waiting = champion_submission.in_flight(board.canonical)
    assert waiting.stage == "intent" and waiting.waiting["ask"]

    champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)  # no answer yet: nothing is sent
    assert champion_submission.in_flight(board.canonical).stage == "intent"

    state = fake_state(board)
    state["answers"] = {waiting.waiting["ask"]: "yes"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
    report = champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)  # one step: sent
    assert report["in_flight"]["stage"] == "submitted"
    report = champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)  # admitted: published
    assert report["in_flight"] is None
    assert report["champion"]["platform_policy_version_id"] == PV_A


def test_a_declined_permit_aborts_the_submission(board):
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        champion_submission.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    ask = champion_submission.in_flight(board.canonical).waiting["ask"]
    state = fake_state(board)
    state["answers"] = {ask: "no"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)

    champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)

    assert champion_submission.in_flight(board.canonical) is None
    [record] = [p for p in champion_submission.load_all(board.canonical) if p.kind == "experiment"]
    assert record.stage == "aborted"


def interrupted_intent(board, platform_id: str) -> champion_submission.ChampionSubmission:
    """An intent published by a run that died before it recorded the platform's answer."""
    record = champion_submission.ChampionSubmission(
        "CS-01K0000000000000000000000Z", "experiment", "V-1", platform_id, LEAGUE, "new",
        experiment="E-1",
    )
    champion_submission._move(board.canonical, record, "intent", "submit E-1")
    return record


def test_an_interrupted_intent_is_aborted_when_the_platform_has_no_submission(board):
    interrupted_intent(board, PV_A)
    champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    [record] = [p for p in champion_submission.load_all(board.canonical) if p.kind == "experiment"]
    assert record.stage == "aborted"
    assert len(board.sm.submissions(PLAYER)) == 1  # never sent blindly


def test_an_interrupted_intent_the_platform_has_becomes_submitted(board):
    interrupted_intent(board, PV_A)
    board.sm.submit(PV_A, LEAGUE)  # the submit reached the platform before the crash
    champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    [record] = [p for p in champion_submission.load_all(board.canonical) if p.kind == "experiment"]
    assert record.submission_id and record.stage in ("submitted", "recorded")


def test_a_hand_submission_is_a_conflict_until_accepted(board):
    board.sm.submit("someone-else:v9", LEAGUE)
    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert "someone-else:v9" in report["conflict"]

    champion_submission.accept(board.canonical, board.sm, LEAGUE, PLAYER)

    assert champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)["conflict"] is None


def test_bootstrap_needs_a_fresh_install(board):
    version = board.store.create("version", {"parents": [], "strategy_sha256": "1" * 64,
                                             "source_commit": "2" * 40,
                                             "artifact_sha256": "3" * 64,
                                             "policy_name": "bootstrap-x"})  # fmt: skip
    with pytest.raises(Refused) as refused:
        champion_submission.bootstrap(board.canonical, board.sm, version, LEAGUE, PLAYER)
    assert refused.value.reason == "champion_exists"


def test_bootstrap_refuses_a_changed_strategy(tmp_path):
    board = ResearchBoard(tmp_path, seed_champion=False)
    meta = {
        "parents": [], "strategy_sha256": "9" * 64, "source_commit": "2" * 40,
        "artifact_sha256": "3" * 64, "policy_name": "bootstrap-x",
        "build": {"source_path": "strategy/STRATEGY.md"},
    }
    version = board.store.create("version", meta)
    board.store.add_event(version, "uploaded", {"policy_version_id": "pv_x"})
    gitops.commit_all(board.canonical, "version")
    gitops.git(board.canonical, "push", "-q", "origin", "main")
    with pytest.raises(Refused) as refused:
        champion_submission.bootstrap(board.canonical, board.sm, version, LEAGUE, PLAYER)
    assert refused.value.reason == "not_shipped_strategy"


def test_the_cli_prints_the_envelope(board):
    experiment, _ = board.reviewed_experiment()
    env = {
        **os.environ,
        "HERMES_HOME": str(board.tmp_path / "home"),
        "RESEARCH_WORKSPACE": str(board.canonical),
        "RESEARCH_GAME": "fakegame",
        "RESEARCH_SOFTMAX": "fake",
        "RESEARCH_PLAYER_ID": PLAYER,
    }
    run = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "champion", "submit", experiment, "--league", LEAGUE],
        capture_output=True, text=True, env=env, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["data"]["stage"] == "submitted"


def test_a_send_that_dies_after_an_approved_permit_is_recovered_not_asked_again(board):
    """The coach said yes, the platform took the submission, the process died before saving
    ``submitted``: the next status finds the submission instead of asking again (R12 #4)."""
    experiment, _ = board.reviewed_experiment()
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"})
    with pytest.raises(Refused):
        champion_submission.submit(board.canonical, sm, experiment, LEAGUE, PLAYER)
    state = fake_state(board)
    state["answers"] = {champion_submission.in_flight(board.canonical).waiting["ask"]: "yes"}
    write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
    real_submit = sm.submit

    def accepted_then_died(*args):
        real_submit(*args)
        raise RuntimeError("died after the platform accepted")

    sm.submit = accepted_then_died
    with pytest.raises(RuntimeError):
        champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)
    sm.submit = real_submit

    report = champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)

    assert report["in_flight"]["stage"] == "submitted" and report["in_flight"]["waiting"] is None
    assert len([s for s in sm.submissions(PLAYER) if s["policy_version_id"] == PV_A]) == 1


def test_status_waits_for_a_live_submit_instead_of_aborting_it(board):
    """The supervisor's status and a chat turn's submit overlap (R12 #3)."""
    import threading

    experiment, _ = board.reviewed_experiment()
    sending, release = threading.Event(), threading.Event()
    real_submit = board.sm.submit

    def slow_submit(*args):
        sending.set()
        release.wait(10)
        return real_submit(*args)

    board.sm.submit = slow_submit
    submitter = threading.Thread(
        target=champion_submission.submit, args=(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    )
    submitter.start()
    assert sending.wait(10)
    checker = threading.Thread(
        target=champion_submission.status, args=(board.canonical, board.sm, LEAGUE, PLAYER)
    )
    checker.start()
    checker.join(1)
    assert checker.is_alive()  # waiting for the champion-submission lock
    release.set()
    submitter.join(10)
    checker.join(10)

    [record] = [p for p in champion_submission.load_all(board.canonical) if p.kind == "experiment"]
    assert "aborted" not in [entry["stage"] for entry in record.history]


def test_a_researchers_stale_clone_sees_the_published_champion(board, tmp_path):
    """A researcher's clone moves only when it integrates; the conflict check reads canonical
    ``main`` (R12 #2)."""
    researcher = tmp_path / "researcher"
    gitops.git(tmp_path, "clone", "-q", str(board.origin), str(researcher))
    experiment, _ = board.reviewed_experiment()
    champion_submission.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)

    assert champion_submission.baseline_problem(researcher, board.sm, LEAGUE, PLAYER, PV_A) is None


def test_a_rejected_champion_publish_is_redone_with_the_strategy(board, monkeypatch):
    """The push of ``published`` and the champion strategy was rejected; the retry must still
    publish the strategy, though the clone already holds it (Codex R14)."""
    from ide_research_core import workspace

    experiment, _ = board.reviewed_experiment()
    champion_submission.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    real_publish = workspace.publish

    def reject_published(clone, files, message):
        if message.endswith(": published"):
            monkeypatch.setattr(gitops, "push_with_rejections", lambda *_: (False, ["main"]))
        return real_publish(clone, files, message)

    monkeypatch.setattr(workspace, "publish", reject_published)
    with pytest.raises(workspace.WorkspaceError):
        champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    monkeypatch.undo()

    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)

    assert report["in_flight"] is None
    champion = gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH)
    assert champion.strip() == f"# Candidate for {experiment}".encode()


@pytest.mark.parametrize("seed_champion", [False, True])
@pytest.mark.parametrize("permit", [False, True])
@pytest.mark.parametrize("source_path", ["strategy/requested/STRATEGY.md", "strategy/requested/policy.bas"])
def test_requested_version_submission_needs_no_experiment_and_preserves_strategy(tmp_path, seed_champion, permit, source_path):
    board = ResearchBoard(tmp_path, seed_champion=seed_champion)
    version, source_path, source = uploaded_requested_version(board, source_path)
    path = board.canonical / source_path
    shipped = gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH)
    sm = FakeSoftmax(board.canonical, {FAULTS_ENV: "permit=submit"}) if permit else board.sm
    if permit:
        with pytest.raises(Refused) as refused:
            champion_submission.submit_requested(
                board.canonical, sm, version, source_path,
                "Build and submit this policy to my league", LEAGUE, PLAYER,
            )
        assert refused.value.reason == "permit_required"
        waiting = champion_submission.in_flight(board.canonical)
        state = fake_state(board)
        state["answers"] = {waiting.waiting["ask"]: "yes"}
        write_json(board.canonical / ".supervisor/runtime/fake_softmax.json", state)
        result = champion_submission.status(board.canonical, sm, LEAGUE, PLAYER)["in_flight"]
    else:
        result = champion_submission.submit_requested(
            board.canonical, sm, version, source_path,
            "Build and submit this policy to my league", LEAGUE, PLAYER,
        )
    assert result["kind"] == "requested" and result["stage"] == "submitted"
    assert result["experiment"] is None
    path.write_text("# Later edit\n")
    gitops.commit_all(board.canonical, "later source edit")
    gitops.git(board.canonical, "push", "-q", "origin", "main")
    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert report["champion"]["platform_policy_version_id"] == PV_A
    # Only a build compiled from a STRATEGY.md replaces it; a .bas source is a prebuilt artifact.
    expected = source if source_path.endswith("STRATEGY.md") else shipped
    assert gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH) == expected
    saved = f"champion-submissions/sources/{result['id']}.md"
    assert gitops.show(board.origin, "main", saved) == source
    assert not board.store.find("experiment")
    before = board.sm.submissions(PLAYER)
    repeated = champion_submission.submit_requested(
        board.canonical, board.sm, version, f"champion-submissions/sources/{result['id']}.md",
        "Build and submit this policy to my league", LEAGUE, PLAYER,
    )
    assert repeated["id"] == result["id"] and repeated["stage"] == "published"
    assert board.sm.submissions(PLAYER) == before


def uploaded_requested_version(board, source_path="strategy/requested/STRATEGY.md"):
    import hashlib

    source = b"# Explicitly requested candidate\n"
    path = board.canonical / source_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source)
    version = board.store.create("version", {
        "parents": [], "strategy_sha256": hashlib.sha256(source).hexdigest(),
        "source_commit": "2" * 40, "artifact_sha256": "3" * 64,
        "policy_name": "requested", "build": {"source_path": source_path},
    })
    board.store.add_event(version, "uploaded", {"policy_version_id": PV_A})
    gitops.commit_all(board.canonical, "uploaded requested version")
    gitops.git(board.canonical, "push", "-q", "origin", "main")
    return version, source_path, source


@pytest.mark.parametrize("strategy", ["strategy/requested/input.bas", "strategy/requested/live.bas"])
def test_requested_prebuilt_version_accepts_its_recorded_inputs_and_keeps_strategy(board, strategy):
    """A prebuilt build (GotA ``--prebuilt``) records the artifact as ``source_path`` and its
    hash as ``strategy_sha256``, plus the committed input it was made from. Either is its
    source, and neither may replace ``strategy/STRATEGY.md``."""
    import hashlib

    files = {"strategy/requested/input.bas": b"10 PRINT 1\n", "strategy/requested/live.bas": b"10 PRINT 2\n"}
    for name, content in files.items():
        (board.canonical / name).parent.mkdir(parents=True, exist_ok=True)
        (board.canonical / name).write_bytes(content)
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    version = board.store.create("version", {
        "parents": [], "strategy_sha256": hashes["strategy/requested/live.bas"],
        "source_commit": "2" * 40, "artifact_sha256": hashes["strategy/requested/live.bas"],
        "policy_name": "requested",
        "build": {"source_path": "strategy/requested/live.bas", "source_hashes": hashes,
                  "compiler": {"mode": "prebuilt"}},
    })
    board.store.add_event(version, "uploaded", {"policy_version_id": PV_A})
    gitops.commit_all(board.canonical, "uploaded prebuilt version")
    gitops.git(board.canonical, "push", "-q", "origin", "main")
    shipped = gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH)

    result = champion_submission.submit_requested(
        board.canonical, board.sm, version, strategy, "Submit this BASIC policy", LEAGUE, PLAYER,
    )
    assert result["stage"] == "submitted"
    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert report["champion"]["platform_policy_version_id"] == PV_A
    assert gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH) == shipped
    saved = f"champion-submissions/sources/{result['id']}.md"
    assert gitops.show(board.origin, "main", saved) == files[strategy]


@pytest.mark.parametrize("fault,reason", [("source", "strategy_mismatch"), ("request", "missing_request")])
@pytest.mark.parametrize("source_path", ["strategy/requested/STRATEGY.md", "strategy/requested/policy.bas"])
def test_requested_submission_refuses_invalid_provenance_before_platform_write(board, fault, reason, source_path):
    version, source_path, _ = uploaded_requested_version(board, source_path)
    if fault == "source":
        (board.canonical / source_path).write_text("# Wrong build\n")
        gitops.commit_all(board.canonical, "wrong source")
        gitops.git(board.canonical, "push", "-q", "origin", "main")
    before = board.sm.submissions(PLAYER)
    with pytest.raises(Refused) as refused:
        champion_submission.submit_requested(
            board.canonical, board.sm, version, source_path,
            "" if fault == "request" else "Submit this policy", LEAGUE, PLAYER,
        )
    assert refused.value.reason == reason
    assert champion_submission.in_flight(board.canonical) is None
    assert board.sm.submissions(PLAYER) == before


def test_requested_submission_recovers_lost_response_without_resubmitting(board, monkeypatch):
    version, source_path, source = uploaded_requested_version(board)
    send = board.sm.submit
    before = board.sm.submissions(PLAYER)

    def lost_response(*args):
        send(*args)
        raise RuntimeError("connection lost after server accepted submission")

    monkeypatch.setattr(board.sm, "submit", lost_response)
    with pytest.raises(RuntimeError, match="connection lost"):
        champion_submission.submit_requested(
            board.canonical, board.sm, version, source_path, "Submit this policy", LEAGUE, PLAYER,
        )
    assert champion_submission.in_flight(board.canonical).stage == "intent"
    champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    report = champion_submission.status(board.canonical, board.sm, LEAGUE, PLAYER)
    assert report["champion"]["platform_policy_version_id"] == PV_A
    assert len(board.sm.submissions(PLAYER)) == len(before) + 1
    assert gitops.show(board.origin, "main", champion_submission.CHAMPION_PATH) == source


def _records_show(capsys, board, id_):
    from ide_research_core.cli import main

    code = main(["records", "show", id_, "--root", str(board.canonical)])
    return code, json.loads(capsys.readouterr().out)


def test_records_show_reads_champion_submissions_by_cs_legacy_p_and_platform_ids(board, capsys):
    experiment, _ = board.reviewed_experiment()
    submitted = champion_submission.submit(board.canonical, board.sm, experiment, LEAGUE, PLAYER)
    cs_id = submitted["id"]

    for id_ in (cs_id, "P-" + cs_id.removeprefix("CS-"), submitted["submission_id"]):
        code, env = _records_show(capsys, board, id_)
        assert code == 0, env
        assert env["data"]["id"] == cs_id and env["data"]["entity"] == "champion_submission"
        assert env["data"]["meta"]["experiment"] == experiment


def test_records_show_names_the_right_tool_for_an_unrecorded_platform_id(board, capsys):
    code, env = _records_show(capsys, board, "sub_unknown")
    assert code == 1 and "latest_league_result" in env["error"]["message"]
    code, env = _records_show(capsys, board, "CS-01K6Z3V8QX0000000000000000")
    assert code == 1 and "not found" in env["error"]["message"]


def test_records_show_resolves_an_ar_reference(board, capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_GAME", raising=False)
    code, env = _records_show(capsys, board, f"ar://hypothesis/{board.hypothesis}")
    assert code == 0, env
    assert env["data"]["path"] == f"hypotheses/{board.hypothesis}.md"
    assert "Mechanism." in env["data"]["content"]

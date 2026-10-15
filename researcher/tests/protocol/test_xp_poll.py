"""xp-poll (Plan 01 Task 3.9; A10, A12): evidence from completed XP requests, the review
card when an evaluation finishes, and relaunch of ``awaiting-credits`` (and
``awaiting-permit``) experiments after the
credit backoff with no duplicate XP request."""

import argparse
import dataclasses
import datetime
import json

import pytest
from support.keeper_host import (
    BASELINE_PV,
    CANDIDATE_PV,
    DESIGN,
    GAME_VERSION,
    LEAGUE,
    Host,
    evaluating_experiment,
    launch_all,
)

from autoresearch.io.docs import read_json, write_json
from autoresearch.runctx import RunContext
from autoresearch.runtime import rebuild, xp_poll
from autoresearch.runtime.markers import relaunch_marker
from autoresearch.softmax_tool import cli as softmax_cli
from autoresearch.softmax_tool import xp
from autoresearch.softmax_tool.protocol import XpStatus
from autoresearch.softmax_tool.xp import ArtifactsIncomplete
from autoresearch.stats.ingest import EpisodeRow, ingest, pair

T0 = datetime.datetime.now(datetime.UTC)
TOPIC = {
    "title": "Score",
    "objective": "Score more.",
    "metrics": [{"name": "score", "direction": "up"}],
    "guardrails": [],
    "origin": {"kind": "human"},
    "priority": 1,
    "status": "open",
}


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    host = Host(tmp_path)
    host.tick(at(0))
    return host


def _experiment(host: Host, status: str) -> str:
    """An experiment bound to a candidate, walked to ``status`` and committed in canonical."""
    store = host.store
    topic = store.create("topic", dict(TOPIC))
    hypothesis = store.create(
        "hypothesis",
        {
            "statement": "S.",
            "prediction": "P.",
            "topics": [topic, "T-01K6Z3V8QXAAAAAAAAAAAAAAAA"],  # the second does not exist
            "lever": "SK.x",
            "state": "proposed",
        },
        "H.\n",
    )
    walk = ("built", "uploaded", "evaluating")
    experiment = evaluating_experiment(store, hypothesis, walk[: walk.index(status) + 1])
    # Experiments run only with a current noise record (C10), so their cards are admitted.
    noise = host.canonical / "stats" / "metric-noise.yaml"
    noise.parent.mkdir(exist_ok=True)
    noise.write_text(f"game_version: '{GAME_VERSION}'\nmetrics: {{}}\n")
    host.runtime_commit(f"experiment {experiment} {status}")
    return experiment


def _launch_all(host: Host, experiment: str) -> list:
    return launch_all(host.sm, host.store, experiment)


def _poll(host: Host, now: datetime.datetime, sm=None) -> xp_poll.XpPollRun:
    host.now = now
    return xp_poll.run(host.ctx, sm or host.sm, host.game, now, env=host.env, queue=host.queue)


def _evidence(host: Host) -> list[dict]:
    index = host.canonical / "evidence" / "index"
    return [read_json(path) for path in sorted(index.glob("EV-*.json"))]


def _rows(host: Host, experiment: str) -> list[EpisodeRow]:
    rows = []
    for path in sorted((host.canonical / "evidence" / "xp" / experiment).glob("*/rows.jsonl")):
        for line in path.read_text().splitlines():
            data = json.loads(line)
            row = EpisodeRow(**data)
            row.seats, row.opponents = tuple(row.seats), tuple(row.opponents)
            rows.append(row)
    return rows


def _review_requests(host: Host, experiment: str) -> list[dict]:
    marker = f"[review:{experiment}:n"
    return [r for r in host.requests() if marker in r.get("spec", {}).get("why", "")]


def _set_fake_state(host: Host, **values) -> None:
    path = host.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
    state = read_json(path) if path.exists() else host.sm._initial_state()
    state.update(values)
    write_json(path, state)


# --- evaluating: evidence and the review card ---


def test_completed_evaluation_writes_evidence_and_requests_one_review(host):
    experiment = _experiment(host, "evaluating")
    handles = _launch_all(host, experiment)

    result = _poll(host, at(1))

    assert sorted(result.evidence) == sorted(h.request_id for h in handles)
    evidence = _evidence(host)
    assert {e["source_ids"]["xreq"] for e in evidence} == {h.request_id for h in handles}
    assert all(e["kind"] == "xp-result" and e["game_version"] == GAME_VERSION for e in evidence)
    for entry in evidence:
        assert (host.canonical / entry["path"]).exists()
    rows = _rows(host, experiment)
    assert len(rows) == len(handles)
    by_arm = {h.request_id: h for h in handles}
    for row in rows:
        handle = by_arm[row.episode_id.split(":")[0]]
        assert row.seed == handle.seed
        assert row.policy_version == (CANDIDATE_PV if handle.arm == "candidate" else BASELINE_PV)
        assert set(row.metrics) == {"score", "errors"} and row.status == "completed"
        # The fake results cover every roster seat (ours and opponents'); the row is the
        # mean over our design seats only (C3 seat contract).
        results = read_json(
            host.canonical / "evidence" / "xp" / experiment / handle.request_id / "results.json"
        )
        assert {e["seat"] for e in results["episodes"]} > set(DESIGN.seats)
        ours = [e for e in results["episodes"] if e["seat"] in DESIGN.seats]
        assert row.metrics["score"] == sum(e["score"] for e in ours) / len(ours)
    kept, report = ingest(rows, DESIGN)
    assert report.kept == len(rows)
    assert len(pair(kept, DESIGN, CANDIDATE_PV)) == len(DESIGN.seeds)
    [review] = _review_requests(host, experiment)
    meta, _ = host.store.load(experiment)
    [topic] = host.store.find("topic")
    assert review["spec"]["refs"] == [
        f"ar://experiment/{experiment}",
        f"ar://hypothesis/{meta['hypothesis']}",
        f"ar://topic/{topic}",  # existing Topics only, so admission resolves every ref
    ]
    assert (
        review["spec"]["priority"] == "P1" and f"evidence/xp/{experiment}" in review["spec"]["why"]
    )
    assert host.remote_head() == host.head() and host.clean()

    head = host.head()
    again = _poll(host, at(2))
    assert again.evidence == [] and again.reviews == [] and host.head() == head

    keeper = host.tick(at(3))
    assert any(r.ok for r in keeper.admitted)
    [card] = [c for c in host.queue.list() if c.spec.title == f"Review {experiment}"]
    assert card.state == "pending"


def test_running_requests_are_polled_again_later(host):
    experiment = _experiment(host, "evaluating")
    _launch_all(host, experiment)
    _set_fake_state(host, polls_to_complete=2)

    first = _poll(host, at(1))
    assert first.evidence == [] and first.reviews == []

    second = _poll(host, at(2))
    assert len(second.evidence) == 4 and len(_review_requests(host, experiment)) == 1


class OddPlatform:
    """FakeSoftmax with an unknown status for one request and artifacts not ready once."""

    def __init__(self, inner, odd_request: str):
        self.inner, self.odd_request, self.incomplete_once = inner, odd_request, True

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def xp_status(self, handle):
        if handle.request_id == self.odd_request:
            return XpStatus("pending", 0, 1, raw_status="queued-ish", warning="unknown status")
        return self.inner.xp_status(handle)

    def xp_artifacts(self, handle, dest):
        if self.incomplete_once:
            self.incomplete_once = False
            raise ArtifactsIncomplete("replay still uploading")
        return self.inner.xp_artifacts(handle, dest)


def test_unknown_status_keeps_polling_and_incomplete_artifacts_retry(host):
    experiment = _experiment(host, "evaluating")
    handles = _launch_all(host, experiment)
    odd = OddPlatform(host.sm, handles[0].request_id)

    first = _poll(host, at(1), odd)

    assert any("unknown status" in w for w in first.warnings)
    assert any("replay still uploading" in w for w in first.warnings)
    assert len(first.evidence) == 2  # one unknown, one incomplete
    assert first.reviews == []

    second = _poll(host, at(2), odd)
    assert len(second.evidence) == 1 and second.reviews == []  # still waiting on the odd one

    third = _poll(host, at(3))
    assert len(third.evidence) == 1 and len(third.reviews) == 1


class UnknownArm:
    """FakeSoftmax whose recovered requests carry an arm the design does not have."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def xp_recover(self, experiment):
        return [dataclasses.replace(h, arm=None) for h in self.inner.xp_recover(experiment)]


def test_unknown_arm_or_missing_binding_fails_instead_of_mislabelling(host):
    experiment = _experiment(host, "evaluating")
    _launch_all(host, experiment)

    unknown = _poll(host, at(1), UnknownArm(host.sm))
    assert "unknown arm" in unknown.failures[experiment] and unknown.evidence == []

    assert _evidence(host) == []

    # The record store refuses to unset a binding, so check the lookup itself.
    [handle, *_] = host.sm.xp_recover(experiment)
    unbound = {"id": experiment, "candidate_binding": None}
    with pytest.raises(ValueError, match="candidate_binding"):
        xp_poll._arm_policy(unbound, DESIGN, dataclasses.replace(handle, arm="candidate"))


# --- awaiting-credits: relaunch after the backoff, no duplicate XP ---


def _champion_is_the_baseline(host: Host) -> None:
    """The league shows the design's baseline as our champion: ``xp create`` launches only
    against the current champion (``promotion.baseline_problem``)."""
    host.sm.submit(BASELINE_PV, LEAGUE)


def _cli_args(experiment: str) -> argparse.Namespace:
    return argparse.Namespace(experiment=experiment, batch=1, attempt=1, batches=1)


def test_credit_denial_relaunches_after_backoff_without_duplicate_xp(host, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    experiment = _experiment(host, "uploaded")
    _champion_is_the_baseline(host)
    meta, _ = host.store.load(experiment)
    planned = softmax_cli.xp_specs(
        meta["evaluation_design"], meta["candidate_binding"], LEAGUE, 1, 1
    )
    # The first launch (in canonical here, as after its card merged) runs out of credits.
    _set_fake_state(host, credits=2)
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    denied = softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))
    assert denied["credit_denied"] and len(denied["created"]) == 2
    host.runtime_commit("launch: credit denied")
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "awaiting-credits"
    assert meta["lifecycle"]["credits_at_denial"] == 0  # read right after the denial (A10)
    backoff_end = datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])

    # Before the backoff ends: evidence for the partial batch, but no relaunch (0 credits).
    early = _poll(host, backoff_end - datetime.timedelta(minutes=5))
    assert early.launches == [] and len(early.evidence) == 2
    # After it, still no credits (the balance is readable): no relaunch.
    assert _poll(host, backoff_end + datetime.timedelta(minutes=1)).launches == []

    _set_fake_state(host, credits=100)
    due = _poll(host, backoff_end + datetime.timedelta(minutes=2))
    [launch] = due.launches
    assert f"[{relaunch_marker(experiment, 1, meta['lifecycle']['next_launch_after'])}]" in (
        launch.why
    )
    assert "xp recover" in launch.why and "--attempt 1" in launch.why
    assert _poll(host, backoff_end + datetime.timedelta(minutes=3)).launches == []  # once

    # queue-keeper admits the launch card; its worker relaunches the same attempt.
    host.tick(backoff_end + datetime.timedelta(minutes=4))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    relaunched = softmax_cli._xp_create(worker.ctx, host.sm, _cli_args(experiment))
    assert len(relaunched["created"]) == 2 and not relaunched["credit_denied"]
    assert len(host.sm.xp_recover(experiment)) == len(planned)  # no duplicate XP request
    worker.finish()
    host.tick(backoff_end + datetime.timedelta(minutes=5))
    assert host.store.load(experiment)[0]["status"] == "evaluating"

    final = _poll(host, backoff_end + datetime.timedelta(minutes=6))
    assert len(final.evidence) == 2 and len(final.reviews) == 1
    assert len(_evidence(host)) == len(planned)


def test_relaunch_happens_when_credits_return_before_the_backoff(host, monkeypatch):
    """A10: a readable balance above 0 relaunches at once; the backoff is only a fallback."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    experiment = _experiment(host, "uploaded")
    _champion_is_the_baseline(host)
    _set_fake_state(host, credits=0)
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    assert softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))["credit_denied"]
    host.runtime_commit("launch: credit denied")
    meta, _ = host.store.load(experiment)
    backoff_end = datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])

    _set_fake_state(host, credits=100)
    [launch] = _poll(host, backoff_end - datetime.timedelta(minutes=20)).launches
    assert "100 credits available, about" in launch.why


def test_at_most_one_relaunch_per_poll(host, monkeypatch):
    """A refill must not start every waiting experiment on the same poll (A10)."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    waiting = []
    for _ in range(2):
        experiment = _experiment(host, "uploaded")
        _champion_is_the_baseline(host)
        _set_fake_state(host, credits=0)
        assert softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))["credit_denied"]
        host.runtime_commit("launch: credit denied")
        waiting.append(experiment)
    meta, _ = host.store.load(waiting[0])
    early = datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])
    _set_fake_state(host, credits=1000)
    first = _poll(host, early - datetime.timedelta(minutes=20))
    [launch] = first.launches
    assert waiting[0] in launch.why  # the oldest first
    [second] = _poll(host, early - datetime.timedelta(minutes=19)).launches
    assert waiting[1] in second.why


T_DUE = datetime.datetime(2026, 10, 9, 12, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    "credits, at_denial, need, minutes_past_backoff, due",
    [
        (50.0, 0, 40, -10, True),  # covers the remaining requests: at once
        (50.0, None, 40, -10, True),  # balance unreadable at denial, readable now
        (5.0, 0, 40, -10, False),  # a trickle of refill does not cover it: wait
        (5.0, 0, 40, 10, True),  # ... until the backoff passes (the fallback)
        (0.0, 0, 0, 10, False),  # readable zero never relaunches
        (None, 0, 40, -10, False),  # unreadable: the backoff decides
        (None, 0, 40, 10, True),
        (50.0, 60.0, 40, -10, False),  # not above the balance the denial came with
        (70.0, 60.0, 40, -10, True),
    ],
)
def test_relaunch_due(credits, at_denial, need, minutes_past_backoff, due):
    lifecycle = {"next_launch_after": T_DUE.isoformat(), "credits_at_denial": at_denial}
    now = T_DUE + datetime.timedelta(minutes=minutes_past_backoff)
    assert xp_poll.relaunch_due(lifecycle, credits, now, need) is due


def test_relaunch_need_counts_the_remaining_requests():
    experiment = {
        "evaluation_design": {"seeds": [1, 2, 3]},
        "xp_requests": [{"seed": 1, "arm": "candidate"}, {"seed": 1, "arm": "baseline"}],
    }
    assert xp_poll.relaunch_need(experiment, 2.5, 100) == 4 * 2.5
    assert xp_poll.relaunch_need(experiment, None, 100) == 100
    assert xp_poll.min_relaunch_credits({xp_poll.MIN_CREDITS_ENV: "7"}) == 7
    assert xp_poll.min_relaunch_credits({xp_poll.MIN_CREDITS_ENV: "x"}) == 100


# --- awaiting-permit: the coach's IDE Controls refused an XP request (Softmax hosting) ---


class _RecordingCreates:
    """Records every ``xp_create`` call's arguments (the request body is built from them)."""

    def __init__(self, inner):
        self.inner, self.creates = inner, []

    def xp_create(self, *args, **kwargs):
        self.creates.append((args, kwargs))
        return self.inner.xp_create(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _body(call) -> dict:
    (experiment, batch, attempt, spec), kwargs = call
    return xp.request_body(experiment, batch, attempt, spec, kwargs["seed"], kwargs["arm"])


def _coach_messages(host: Host) -> list[dict]:
    keys = ("permit-needed:", "league-not-chosen:", "coach-declined:")
    return [m for m in host.outbox() if str(m.get("runtime_key", "")).startswith(keys)]


def _refused_launch(host: Host, sm, experiment: str) -> dict:
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    refused = softmax_cli._xp_create(launcher, sm, _cli_args(experiment))
    host.runtime_commit("launch: refused for the coach")
    return refused


def _answer(host: Host, answer: str, keys) -> None:
    state = read_json(host.canonical / ".supervisor" / "runtime" / "fake_softmax.json")
    for key in keys:
        state.setdefault("answers", {})[key] = answer
    _set_fake_state(host, answers=state["answers"])


def _awaiting_permit(host: Host, monkeypatch, faults: str = "permit=spend"):
    """An uploaded experiment whose launch the coach's IDE Controls refused: ``(experiment,
    the recording Softmax of that launch, its permit_wait, the backoff's end)``."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    experiment = _experiment(host, "uploaded")
    _champion_is_the_baseline(host)
    host.set_faults(faults)  # the IDE Controls ask before XP requests
    refusing = _RecordingCreates(host.sm)
    refused = _refused_launch(host, refusing, experiment)
    assert refused["permit_required"] and not refused["credit_denied"]
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "awaiting-permit"
    wait = meta["lifecycle"]["permit_wait"]
    return experiment, refusing, wait, datetime.datetime.fromisoformat(
        meta["lifecycle"]["next_launch_after"]
    )


def test_permit_refusal_files_every_ask_asks_the_coach_once_and_relaunches_on_the_yes(
    host, monkeypatch
):
    experiment, refusing, wait, backoff_end = _awaiting_permit(host, monkeypatch)
    meta, _ = host.store.load(experiment)
    planned = 2 * len(meta["evaluation_design"]["seeds"])
    assert len(wait["asks"]) == planned  # one run filed every ask of the batch
    keys = [entry["ask"] for entry in wait["asks"]]

    # One help request for the batch; while the asks wait, no card, even after the backoff.
    first = _poll(host, at(1))
    assert first.help_requests == [experiment] and first.launches == []
    [asked] = _coach_messages(host)
    assert asked["runtime_key"].startswith(f"permit-needed:{experiment}:b1:a1:")
    assert asked["kind"] == "help_request" and asked["expects_response"] is True
    assert f"Softmax filed {planned} permit ask(s)" in asked["body"]
    assert "remove “spend”" in asked["body"] and "“spend-over-N”" in asked["body"]
    assert asked["about"]["asks"] == ", ".join(keys)
    later = _poll(host, backoff_end + datetime.timedelta(hours=1))
    assert later.help_requests == [] and later.launches == []

    # Some answers in: still nothing. All yes: one relaunch at once, before any backoff.
    _answer(host, "yes", keys[:-1])
    assert _poll(host, at(2)).launches == []
    _answer(host, "yes", keys[-1:])
    [launch] = _poll(host, at(3)).launches
    assert "--attempt 1" in launch.why and f"approved all {planned} permit ask(s)" in launch.why
    host.tick(at(4))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    approved = _RecordingCreates(host.sm)  # the fault stays: each yes grants its own request
    relaunched = softmax_cli._xp_create(worker.ctx, approved, _cli_args(experiment))
    assert not relaunched["permit_required"] and relaunched["missing"] == []
    # Every refused request goes again with the identical body and idempotency key.
    assert [_body(call) for call in approved.creates] == [_body(call) for call in refusing.creates]
    assert [_body(call)["idempotency_key"] for call in approved.creates] == [
        entry["request"] for entry in wait["asks"]
    ]
    worker.finish()
    host.tick(at(5))
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "evaluating" and meta["lifecycle"]["permit_wait"] is None
    assert len(_coach_messages(host)) == 1


def test_coach_hold_and_on_request_start_hold_back_the_relaunch_but_not_the_answers(
    host, monkeypatch
):
    """The coach hold, and an on-request agent not started yet, make no relaunch card (as
    they stop planning); the help request and the coach's answers are still handled, and the
    relaunch comes on the first run after the hold is lifted."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys)
    hold = host.canonical / ".supervisor" / "ledger" / "coach-hold.json"
    write_json(hold, {"held_at": "2026-10-09T00:00:00Z", "by": "coach:amy", "tasks": {}})

    held = _poll(host, at(1))
    assert held.help_requests == [experiment] and held.launches == []
    host.env["AUTORESEARCH_AUTONOMY"] = "on-request"
    hold.unlink()
    assert _poll(host, at(2)).launches == []  # not started: still no card

    host.env["AUTORESEARCH_AUTONOMY"] = "always"
    [launch] = _poll(host, at(3)).launches
    assert launch.title == f"Launch {experiment}"


def test_a_no_under_the_coach_hold_still_stops_the_experiment(host, monkeypatch):
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    hold = host.canonical / ".supervisor" / "ledger" / "coach-hold.json"
    write_json(hold, {"held_at": "2026-10-09T00:00:00Z", "by": "coach:amy", "tasks": {}})
    _answer(host, "no", [wait["asks"][0]["ask"]])

    stopped = _poll(host, at(1))

    assert stopped.stopped == [experiment] and stopped.launches == []
    assert host.store.load(experiment)[0]["status"] == "stopped"


def test_permit_no_stops_the_experiment_with_one_note_and_never_asks_again(host, monkeypatch):
    experiment, _, wait, backoff_end = _awaiting_permit(host, monkeypatch)
    _poll(host, at(1))
    _answer(host, "no", [wait["asks"][0]["ask"]])
    stopped = _poll(host, at(2))
    assert stopped.stopped == [experiment] and stopped.launches == []
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "stopped" and meta["lifecycle"]["stopped"]["by"] == "coach"
    assert f"xp-poll: {experiment} stopped" in "\n".join(host.subjects())
    [_, note] = _coach_messages(host)
    assert note["kind"] == "note" and note["runtime_key"] == f"coach-declined:{experiment}:b1:a1"
    assert "stopped that experiment with 0 of its" in note["body"]
    after = _poll(host, backoff_end + datetime.timedelta(days=1))
    assert after.launches == [] and after.help_requests == [] and len(_coach_messages(host)) == 2
    asks = read_json(host.canonical / ".supervisor" / "runtime" / "fake_softmax.json")["asks"]
    assert sorted(asks.values()) == sorted(entry["ask"] for entry in wait["asks"])  # no new ask


def test_unreadable_answers_fall_back_to_the_backoff(host, monkeypatch):
    faults = "permit=spend;asks_read=fail"
    _, _, _, backoff_end = _awaiting_permit(host, monkeypatch, faults)
    assert _poll(host, backoff_end - datetime.timedelta(minutes=1)).launches == []
    [launch] = _poll(host, backoff_end + datetime.timedelta(minutes=1)).launches
    assert "could not be read" in launch.why


def test_over_the_cap_waits_for_the_utc_day_even_after_a_yes(host, monkeypatch):
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch, "permit=spend;over_cap=1")
    _answer(host, "yes", [entry["ask"] for entry in wait["asks"]])
    not_before = datetime.datetime.fromisoformat(wait["not_before"])
    assert _poll(host, not_before - datetime.timedelta(minutes=1)).launches == []
    [asked] = _coach_messages(host)
    assert "waits for the next UTC day before it tries them again" in asked["body"]
    [launch] = _poll(host, not_before + datetime.timedelta(minutes=1)).launches
    assert experiment in launch.why


def test_one_reminder_after_a_day_and_no_backoff_card_while_an_ask_is_known_to_wait(
    host, monkeypatch
):
    """N6: an ask still waiting a day after the batch was refused gets one reminder, never
    more; a known waiting ask holds back the backoff relaunch even when others are
    unreadable."""
    experiment, _, wait, backoff_end = _awaiting_permit(host, monkeypatch)
    since = datetime.datetime.fromisoformat(wait["since"])
    keys = [entry["ask"] for entry in wait["asks"]]
    real = host.sm.permit_answer
    monkeypatch.setattr(
        host.sm, "permit_answer", lambda key: None if key == keys[0] else real(key)
    )
    assert _poll(host, backoff_end + datetime.timedelta(minutes=1)).launches == []
    assert _poll(host, since + datetime.timedelta(hours=23)).help_requests == []
    reminded = _poll(host, since + datetime.timedelta(hours=24, minutes=1))
    assert reminded.help_requests == [experiment] and reminded.launches == []
    assert _poll(host, since + datetime.timedelta(hours=48)).help_requests == []
    first, reminder = _coach_messages(host)
    assert reminder["title"] == "Still waiting for your approval in the IDE Controls"
    assert reminder["runtime_key"] == first["runtime_key"] + ":reminder"


def test_only_used_asks_are_not_read_again(host, monkeypatch):
    """N7, narrowed by Codex round 4: the coach can change a yes or a no until it is used, so
    those are read every run; only a used yes is final and skipped."""
    _, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys[1:-1])
    _answer(host, "used", keys[:1])
    reads = []
    real = host.sm.permit_answer
    monkeypatch.setattr(host.sm, "permit_answer", lambda key: reads.append(key) or real(key))
    assert _poll(host, at(1)).launches == []
    assert sorted(reads) == sorted(keys)
    reads.clear()
    assert _poll(host, at(2)).launches == []
    assert reads == keys[1:]  # every ask but the used one


def test_a_yes_changed_to_no_stops_the_experiment(host, monkeypatch):
    """Codex round 4 (1): a yes read on an earlier run is not trusted later; the coach changes
    it to no and the experiment stops, with no relaunch."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys[:-1])
    assert _poll(host, at(1)).launches == []  # the last ask still waits
    _answer(host, "no", keys[:1])
    _answer(host, "yes", keys[-1:])
    after = _poll(host, at(2))
    assert after.stopped == [experiment] and after.launches == []
    assert host.store.load(experiment)[0]["status"] == "stopped"


def test_a_known_credit_shortfall_waits_past_the_backoff(host, monkeypatch):
    """Codex round 4 (2): every ask approved, but a readable balance of 1 against requests that
    cost 100 each: no card on any poll, also long after the backoff; a higher balance gives
    one card."""
    experiment, _, keys, _ = _short_of_credits(host, monkeypatch)
    _answer(host, "yes", [keys[0], keys[2], keys[3]])
    monkeypatch.setattr(host.sm, "episode_cost", lambda: 100.0)
    _set_fake_state(host, credits=1)
    short = host.store.load(experiment)[0]["lifecycle"]["permit_wait"]["credits_short"]
    backoff_end = datetime.datetime.fromisoformat(short["next_launch_after"])
    for hours in (0, 1, 9, 30):
        assert _poll(host, backoff_end + datetime.timedelta(hours=hours)).launches == []
    _set_fake_state(host, credits=300)  # three requests left, 100 each
    [launch] = _poll(host, backoff_end + datetime.timedelta(hours=31)).launches
    assert "300 credits available, about 300 needed" in launch.why


def test_new_asks_on_a_relaunch_get_a_new_help_request(host, monkeypatch):
    """N5: a relaunch can be refused again with new asks (the server files ask n+1, as when the
    price outgrew a yes); the new set is announced, not dropped as already published."""
    experiment, refusing, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys)
    [launch] = _poll(host, at(1)).launches
    host.tick(at(2))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    _answer(host, "used", keys[:1])  # this request's yes no longer covers it: a new ask
    again = softmax_cli._xp_create(worker.ctx, refusing, _cli_args(experiment))
    assert again["permit_required"] and [e["ask"] for e in again["permit_asks"]] != keys[:1]
    worker.finish(outcome="partial")
    host.tick(at(3))
    assert host.store.load(experiment)[0]["status"] == "awaiting-permit"
    assert _poll(host, at(4)).help_requests == [experiment]
    first, second = _coach_messages(host)
    assert first["runtime_key"] != second["runtime_key"]
    assert "Softmax filed 1 permit ask(s)" in second["body"]


def test_the_stop_note_counts_each_seed_and_arm_once(host, monkeypatch):
    """N8: a pair with several attempts counts once, so the note never says more than all."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    entry = {"batch": 1, "seed": 1, "arm": "candidate", "policy": "p", "attempt": 1}
    requests = [{**entry, "request_id": "xp_a"}, {**entry, "attempt": 2, "request_id": "xp_b"}]
    host.store.update(experiment, {"xp_requests": requests})
    host.runtime_commit("two attempts of one pair")
    _answer(host, "no", [wait["asks"][0]["ask"]])
    _poll(host, at(1))
    note = _coach_messages(host)[-1]
    assert "with 1 of its 4 XP requests run" in note["body"]


def _short_of_credits(host: Host, monkeypatch):
    """Asks A-D filed for the batch; the coach approves B, and with one credit left the rerun
    sees A refused again, creates B, and is denied credits at C (D is never reached).
    ``(experiment, the refusing Softmax, the asks' keys A-D, the rerun's output)``."""
    experiment, refusing, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    assert len(keys) == 4
    _poll(host, at(1))  # the one help request about A-D
    _answer(host, "yes", keys[1:2])
    _set_fake_state(host, credits=1)
    rerun = _refused_launch(host, refusing, experiment)
    assert rerun["credit_denied"] and len(rerun["created"]) == 1
    return experiment, refusing, keys, rerun


def test_a_partial_retry_keeps_every_earlier_ask_and_a_no_to_one_not_reached_stops_it(
    host, monkeypatch
):
    """Round 4: the ask set is merged by request, never replaced. A, C and D stay tracked
    (only B, now created, drops out), the experiment stays awaiting-permit with the credit
    shortfall inside the wait, and a no to C, which this run never reached, stops it."""
    experiment, _, keys, rerun = _short_of_credits(host, monkeypatch)
    expected = [keys[0], keys[2], keys[3]]
    assert rerun["permit_required"]
    assert [entry["ask"] for entry in rerun["permit_asks"]] == expected
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "awaiting-permit"
    wait = meta["lifecycle"]["permit_wait"]
    assert [entry["ask"] for entry in wait["asks"]] == expected
    assert wait["credits_short"]["reason"] == "no credits"
    assert wait["credits_short"]["credits_at_denial"] == 0
    assert wait["announced"] == sorted(keys)  # no new ask: the same set, the same help request
    assert _poll(host, at(2)).help_requests == []

    _answer(host, "no", keys[2:3])
    stopped = _poll(host, at(3))
    assert stopped.stopped == [experiment] and stopped.launches == []
    assert host.store.load(experiment)[0]["status"] == "stopped"
    assert _coach_messages(host)[-1]["runtime_key"].startswith("coach-declined:")


def test_a_superseded_ask_replaces_its_request_and_the_new_set_is_announced(host, monkeypatch):
    """Round 4: when the server files a new ask for a saved request (its yes was used), that
    request's entry is replaced; the other saved asks stay."""
    experiment, refusing, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _poll(host, at(1))
    _answer(host, "used", keys[:1])  # A's yes no longer covers it: the server files A-2
    _answer(host, "yes", keys[1:2])
    _set_fake_state(host, credits=1)  # B is created, then credits stop the batch at C
    rerun = _refused_launch(host, refusing, experiment)
    asks = [entry["ask"] for entry in rerun["permit_asks"]]
    assert asks[0] != keys[0] and asks[0].endswith("-2") and asks[1:] == [keys[2], keys[3]]
    assert _poll(host, at(2)).help_requests == [experiment]  # A-2 is a new ask


def test_no_card_while_an_ask_waits_even_when_the_credits_return(host, monkeypatch):
    """Codex round 2 (medium): a credit shortfall never bypasses the permit gate. With asks
    open nothing launches when the credits return, nor after the backoff; once all are yes
    it waits for the credits too; then one card for exactly the saved batch, which creates
    the rest. A board rebuild leaves the experiment to xp-poll."""
    experiment, _, keys, _ = _short_of_credits(host, monkeypatch)
    meta, _ = host.store.load(experiment)
    late = datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])
    late += datetime.timedelta(hours=1)
    _set_fake_state(host, credits=100)
    assert _poll(host, at(2)).launches == []
    assert _poll(host, late).launches == []
    assert rebuild._lifecycle_step(host.canonical, meta, host.sm, "e1", late) is None

    _answer(host, "yes", [keys[0], keys[2], keys[3]])
    _set_fake_state(host, credits=0)
    assert _poll(host, at(3)).launches == []  # approved, but still no credits
    _set_fake_state(host, credits=100)
    [launch] = _poll(host, at(4)).launches
    assert "approved all 3 permit ask(s)" in launch.why and "credits available" in launch.why
    assert f"xp create {experiment} --batch 1 --attempt 1 --batches 1" in launch.why
    host.tick(at(5))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    done = softmax_cli._xp_create(worker.ctx, host.sm, _cli_args(experiment))
    assert len(done["created"]) == 3 and done["missing"] == []
    worker.finish()
    host.tick(at(6))
    assert host.store.load(experiment)[0]["status"] == "evaluating"


def test_one_reminder_while_credits_are_short_too(host, monkeypatch):
    """Codex round 2 (low): the 24 h reminder is sent from the one gate, credit shortfall or
    not, and only once."""
    experiment, _, _, _ = _short_of_credits(host, monkeypatch)
    wait = host.store.load(experiment)[0]["lifecycle"]["permit_wait"]
    since = datetime.datetime.fromisoformat(wait["since"])
    reminded = _poll(host, since + datetime.timedelta(hours=24, minutes=1))
    assert reminded.help_requests == [experiment] and reminded.launches == []
    assert _poll(host, since + datetime.timedelta(hours=48)).help_requests == []
    first, reminder = _coach_messages(host)
    assert reminder["runtime_key"] == first["runtime_key"] + ":reminder"
    assert "Softmax filed 3 permit ask(s)" in reminder["body"]  # the asks still open


class _FailsAfterFirstCreate:
    """The first ``xp_create`` goes to the platform; the next one times out."""

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def xp_create(self, *args, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("timed out")
        return self.inner.xp_create(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_each_refusal_is_saved_before_the_next_request(host, monkeypatch):
    """Codex round 3 (1): the server keeps an ask as soon as it refuses a request, so a later
    failure in the batch must not lose it: the run ends awaiting-permit with that ask, and
    xp-poll announces it and stops the experiment on a no."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    experiment = _experiment(host, "uploaded")
    _champion_is_the_baseline(host)
    host.set_faults("permit=spend")
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    with pytest.raises(RuntimeError, match="timed out"):
        softmax_cli._xp_create(launcher, _FailsAfterFirstCreate(host.sm), _cli_args(experiment))
    host.runtime_commit("launch: refused, then a timeout")
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "awaiting-permit"
    [ask] = meta["lifecycle"]["permit_wait"]["asks"]
    assert _poll(host, at(1)).help_requests == [experiment]
    _answer(host, "no", [ask["ask"]])
    assert _poll(host, at(2)).stopped == [experiment]


@pytest.mark.parametrize("answer", ["no", "yes"])
def test_an_evidence_failure_does_not_hold_back_the_coachs_answers(host, monkeypatch, answer):
    """Codex round 3 (2): a failing evaluate binding is still reported, but a no still stops
    the experiment and an all-yes still relaunches it."""
    experiment, _, keys, _ = _short_of_credits(host, monkeypatch)
    _set_fake_state(host, polls_to_complete=1, credits=100)  # B completed: evidence to fetch

    def broken(path):
        raise ValueError("the evaluate binding failed")

    def poll(now):
        host.now = now
        return xp_poll.run(host.ctx, host.sm, host.game, now, env=host.env, evaluate=broken)

    first = poll(at(2))
    assert "the evaluate binding failed" in first.failures[experiment]
    _answer(host, answer, [keys[0], keys[2], keys[3]] if answer == "yes" else keys[2:3])
    after = poll(at(3))
    assert "the evaluate binding failed" in after.failures[experiment]
    if answer == "no":
        assert after.stopped == [experiment]
        assert host.store.load(experiment)[0]["status"] == "stopped"
    else:
        [launch] = after.launches
        assert "approved all 3 permit ask(s)" in launch.why


def test_a_changed_league_is_refused_while_asks_are_open(host, monkeypatch):
    """Codex round 3 (3): the league is part of each request body and ask, so a replay under a
    changed league configuration is refused before anything is sent."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    _answer(host, "yes", [entry["ask"] for entry in wait["asks"]])
    monkeypatch.setattr(softmax_cli, "_league_id", lambda: "another_league")
    recording = _RecordingCreates(host.sm)
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    with pytest.raises(softmax_cli.Refused) as caught:
        softmax_cli._xp_create(launcher, recording, _cli_args(experiment))
    assert caught.value.reason == "permit_league_changed"
    assert caught.value.details == {
        "saved_league": wait["league_id"],
        "configured_league": "another_league",
    }
    assert recording.creates == []


def test_a_relaunch_card_that_ends_without_the_requests_is_retried_then_asks_for_help(
    host, monkeypatch
):
    """Codex round 5 (1): an approved batch whose relaunch card ends partial with no new
    refusal is not stuck. The next poll requests generation 2 of the same relaunch, for the
    identical batch, then generation 3; after that one help request and no more cards."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    _answer(host, "yes", [entry["ask"] for entry in wait["asks"]])
    meta, _ = host.store.load(experiment)
    base = relaunch_marker(experiment, 1, meta["lifecycle"]["next_launch_after"])
    command = f"xp create {experiment} --batch 1 --attempt 1 --batches 1"
    minute = 1
    for generation in (1, 2, 3):
        [launch] = _poll(host, at(minute)).launches
        marker = base if generation == 1 else f"{base}:g{generation}"
        assert f"[{marker}]" in launch.why and command in launch.why
        assert _poll(host, at(minute + 1)).launches == []  # open: covered
        host.tick(at(minute + 2))
        worker = host.claim("experimentation")
        assert worker.card.spec.request_id == launch.request_id
        worker.finish(outcome="partial")  # e.g. a platform error; no new refusal
        host.tick(at(minute + 3))
        minute += 4
    stuck = _poll(host, at(minute))
    assert stuck.launches == []
    [help_request] = [
        m for m in host.outbox() if str(m.get("runtime_key", "")).startswith("relaunch-stuck:")
    ]
    assert help_request["runtime_key"] == f"relaunch-stuck:{base}"
    assert help_request["kind"] == "help_request"
    later = _poll(host, at(minute + 60))
    assert later.launches == []
    assert len([m for m in host.outbox() if "relaunch-stuck:" in str(m.get("runtime_key"))]) == 1
    assert host.store.load(experiment)[0]["status"] == "awaiting-permit"


class _CoachSaysNoMidway:
    """Sends requests to the platform; right after the first one, ``between`` runs (the coach
    changes an answer and xp-poll runs) while this relaunch is still going."""

    def __init__(self, inner, between):
        self.inner, self.between, self.creates = inner, between, 0

    def xp_create(self, *args, **kwargs):
        self.creates += 1
        result = self.inner.xp_create(*args, **kwargs)
        if self.creates == 1:
            self.between()
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_a_no_while_a_relaunch_runs_stops_it_before_the_next_request(host, monkeypatch):
    """Codex round 6 (high): the coach changes a yes to no after the relaunch worker started.
    xp-poll stops the experiment in canonical and stops the launch card; the running ``xp
    create`` checks canonical before its next request, so it spends nothing more and files no
    new ask."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys)
    [launch] = _poll(host, at(1)).launches
    host.tick(at(2))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    polled = []

    def coach_says_no():
        _answer(host, "no", keys[2:3])
        polled.append(_poll(host, at(3)))

    midway = _CoachSaysNoMidway(host.sm, coach_says_no)
    with pytest.raises(softmax_cli.Refused) as caught:
        softmax_cli._xp_create(worker.ctx, midway, _cli_args(experiment))
    assert caught.value.reason == "experiment_closed"
    assert midway.creates == 1  # nothing sent after the stop
    [stop] = polled
    assert stop.stopped == [experiment] and stop.cards_stopped == [worker.card.task_id]
    card = host.queue.get(worker.card.task_id)
    assert card.state == "blocked" and card.block_reason.startswith("coach-stopped:")
    state = read_json(host.canonical / ".supervisor" / "runtime" / "fake_softmax.json")
    assert sorted(state["asks"].values()) == sorted(keys)  # no new ask
    assert state["answers"][keys[1]] == "yes"  # the approved second request was not spent
    assert host.store.load(experiment)[0]["status"] == "stopped"
    host.tick(at(4))  # queue-keeper's blocked handling leaves the stopped card alone
    assert host.queue.get(worker.card.task_id).state == "blocked"


def test_a_declined_refusal_stops_the_batch_at_once(host, monkeypatch):
    """H2: when the server says the coach said no, nothing more of the batch is submitted, the
    wait records the no, ``xp create`` refuses the experiment from then on, and xp-poll stops
    it."""
    experiment, refusing, wait, _ = _awaiting_permit(host, monkeypatch)
    _answer(host, "no", [wait["asks"][0]["ask"]])
    refusing.creates.clear()
    rerun = _refused_launch(host, refusing, experiment)
    assert rerun["permit_declined"] and len(refusing.creates) == 1  # stopped at the first
    meta, _ = host.store.load(experiment)
    # The no replaces its request's ask; the asks the stopped batch never reached stay.
    states = [entry["state"] for entry in meta["lifecycle"]["permit_wait"]["asks"]]
    assert states == ["declined", "waiting", "waiting", "waiting"]
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    with pytest.raises(softmax_cli.Refused) as caught:
        softmax_cli._xp_create(launcher, refusing, _cli_args(experiment))
    assert caught.value.reason == "experiment_closed" and len(refusing.creates) == 1
    assert _poll(host, at(1)).stopped == [experiment]


def test_a_permit_relaunch_names_and_runs_exactly_the_saved_batch(host, monkeypatch):
    """M: the relaunch card runs the batch the asks were filed for (batch 2, attempt 2, batches
    3 here), whatever ``launch_attempts`` says; ``xp create`` refuses any other batch."""
    experiment, _, wait, _ = _awaiting_permit(host, monkeypatch)
    saved = {**wait, "batch": 2, "attempt": 2, "batches": 3}
    meta, _ = host.store.load(experiment)
    host.store.update(experiment, {"lifecycle": {**meta["lifecycle"], "permit_wait": saved}})
    host.runtime_commit("a wait for batch 2 of 3, attempt 2")
    _answer(host, "yes", [entry["ask"] for entry in wait["asks"]])
    [launch] = _poll(host, at(1)).launches
    assert "xp create " + experiment + " --batch 2 --attempt 2 --batches 3" in launch.why
    assert ":2:after:" in launch.why  # the relaunch marker names attempt 2
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    with pytest.raises(softmax_cli.Refused) as caught:
        softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))  # batch 1, attempt 1
    assert caught.value.reason == "permit_batch_mismatch"
    assert caught.value.details == {"batch": 2, "attempt": 2, "batches": 3}
    exact = argparse.Namespace(experiment=experiment, batch=2, attempt=2, batches=3)
    try:
        softmax_cli._xp_create(launcher, host.sm, exact)
    except softmax_cli.Refused as refused:  # later guards may refuse; this one does not
        assert refused.reason != "permit_batch_mismatch"


@pytest.mark.parametrize("cache", ["null", "[]", '"x"', '{"%s": "waiting"}', '{"%s": "bogus"}'])
def test_a_bad_answers_cache_is_ignored_and_the_asks_read_again(host, monkeypatch, cache):
    """R1, R2: only a dict of final answers (yes, used, no) counts."""
    _, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    path = host.canonical / ".supervisor" / "runtime" / xp_poll.ANSWERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.replace("%s", keys[0]) if "%s" in cache else cache)
    _answer(host, "yes", keys)
    [launch] = _poll(host, at(1)).launches  # every ask read again, all yes
    assert launch is not None
    cached = read_json(path)  # no used answer: nothing new is cached as final
    assert not isinstance(cached, dict) or "yes" not in cached.values()


def test_league_choice_refusal_sends_one_help_request_per_league(host, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    _champion_is_the_baseline(host)
    host.set_faults("league=none")
    experiments = []
    for _ in range(2):
        experiment = _experiment(host, "uploaded")
        refused = _refused_launch(host, host.sm, experiment)
        assert refused["league_not_chosen"] and not refused["permit_required"]
        meta, _ = host.store.load(experiment)
        assert meta["status"] == "awaiting-permit"
        assert meta["lifecycle"]["permit_wait"]["kind"] == "league"
        experiments.append(experiment)

    assert _poll(host, at(1)).help_requests == experiments[:1]  # one cause, one request
    assert _poll(host, at(2)).help_requests == []
    [asked] = _coach_messages(host)
    assert asked["runtime_key"] == f"league-not-chosen:{LEAGUE}:none"
    assert asked["title"] == "Choose the agent's league in the IDE Controls"
    assert f"choose the {LEAGUE} league for the agent in the IDE Controls" in asked["body"]
    assert "first 30 minutes after the refusal" in asked["body"]
    assert asked["about"]["league"] == LEAGUE


def test_league_help_text_uses_the_configured_backoff(host, monkeypatch):
    """M3: the worker's ``AUTORESEARCH_CREDIT_BACKOFF_MINUTES`` is the first wait it states."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    monkeypatch.setenv(softmax_cli.BACKOFF_ENV, "7")
    _champion_is_the_baseline(host)
    host.set_faults("league=none")
    experiment = _experiment(host, "uploaded")
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))
    host.runtime_commit("launch: refused for the league")
    _poll(host, at(1))
    [asked] = _coach_messages(host)
    assert "first 7 minutes after the refusal" in asked["body"]


class _Clock(datetime.datetime):
    """Stands in for the softmax tool's ``datetime``, so each denial's backoff is distinct."""

    current = T0

    @classmethod
    def now(cls, tz=None):
        return cls.current


def _relaunch_and_merge(host: Host, experiment: str, batch: int, now: datetime.datetime):
    """xp-poll requests the relaunch; queue-keeper admits it; the worker relaunches batch
    ``batch`` (same attempt) and the card merges. Returns the relaunch request."""
    _set_fake_state(host, credits=100)
    [launch] = _poll(host, now).launches
    host.tick(now + datetime.timedelta(minutes=1))
    worker = host.claim("experimentation")
    assert worker.card.spec.request_id == launch.request_id
    args = argparse.Namespace(experiment=experiment, batch=batch, attempt=1, batches=2)
    assert not softmax_cli._xp_create(worker.ctx, host.sm, args)["credit_denied"]
    worker.finish()
    host.tick(now + datetime.timedelta(minutes=2))
    return launch


def _review_and_merge(host: Host, now: datetime.datetime) -> list:
    """xp-poll requests the review; its card is admitted, worked and merged."""
    reviews = _poll(host, now).reviews
    host.tick(now + datetime.timedelta(minutes=1))
    host.claim("experimentation").finish()
    host.tick(now + datetime.timedelta(minutes=2))
    return reviews


def test_each_denial_gets_its_own_relaunch_and_each_batch_its_own_review(host, monkeypatch):
    """credit_denials resets after a successful batch, so a later denial must not reuse an
    earlier relaunch's marker (it would never be relaunched); a later batch gets its own
    review."""
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    monkeypatch.setattr(softmax_cli, "datetime", _Clock)
    experiment = _experiment(host, "uploaded")
    _champion_is_the_baseline(host)
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")

    def deny(batch: int, at_time: datetime.datetime) -> datetime.datetime:
        _Clock.current = at_time
        _set_fake_state(host, credits=1)
        args = argparse.Namespace(experiment=experiment, batch=batch, attempt=1, batches=2)
        assert softmax_cli._xp_create(launcher, host.sm, args)["credit_denied"]
        host.runtime_commit(f"launch batch {batch}: credit denied")
        meta, _ = host.store.load(experiment)
        assert meta["status"] == "awaiting-credits"
        assert meta["lifecycle"]["credit_denials"] == 1  # reset by the success in between
        return datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])

    # Batch 1: denied, relaunched, reviewed.
    first_backoff = deny(1, at(1))
    first = _relaunch_and_merge(host, experiment, 1, first_backoff + datetime.timedelta(minutes=1))
    assert f"[{relaunch_marker(experiment, 1, first_backoff.isoformat())}]" in first.why
    [review_1] = _review_and_merge(host, first_backoff + datetime.timedelta(minutes=10))
    assert f"[review:{experiment}:n2]" in review_1.why

    # Batch 2: denied again much later; the new denial gets a new relaunch.
    second_backoff = deny(2, at(120))
    assert second_backoff != first_backoff
    second = _relaunch_and_merge(
        host, experiment, 2, second_backoff + datetime.timedelta(minutes=1)
    )
    assert second.request_id != first.request_id
    assert f"[{relaunch_marker(experiment, 1, second_backoff.isoformat())}]" in second.why

    [review_2] = _review_and_merge(host, second_backoff + datetime.timedelta(minutes=10))
    assert f"[review:{experiment}:n4]" in review_2.why
    assert len(_review_requests(host, experiment)) == 2


def test_padded_versions_are_stripped_so_ingest_keeps_the_rows(host, monkeypatch):
    experiment = _experiment(host, "evaluating")
    handles = _launch_all(host, experiment)
    real_artifacts = host.sm.xp_artifacts

    def padded(handle, dest):  # a results.json whose game_version has stray whitespace
        paths = real_artifacts(handle, dest)
        results = read_json(paths[0])
        write_json(paths[0], {**results, "game_version": f" {GAME_VERSION}\n"})
        return paths

    monkeypatch.setattr(host.sm, "xp_artifacts", padded)
    _poll(host, at(1))
    rows = _rows(host, experiment)
    assert len(rows) == len(handles) and {r.game_version for r in rows} == {GAME_VERSION}
    kept, report = ingest(rows, DESIGN)
    assert report.wrong_version == 0 and len(kept) == len(rows)

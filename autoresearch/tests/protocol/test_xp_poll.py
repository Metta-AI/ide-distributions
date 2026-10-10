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
from autoresearch.runtime import xp_poll
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
    return xp_poll.run(host.ctx, sm or host.sm, host.game, now, env=host.env)


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


def test_only_unanswered_asks_are_read_again(host, monkeypatch):
    """N7: a yes (or a no) is final, so it is not read again on later runs."""
    _, _, wait, _ = _awaiting_permit(host, monkeypatch)
    keys = [entry["ask"] for entry in wait["asks"]]
    _answer(host, "yes", keys[:-1])
    reads = []
    real = host.sm.permit_answer
    monkeypatch.setattr(host.sm, "permit_answer", lambda key: reads.append(key) or real(key))
    assert _poll(host, at(1)).launches == []
    assert sorted(reads) == sorted(keys)
    reads.clear()
    assert _poll(host, at(2)).launches == []
    assert reads == keys[-1:]  # only the one still waiting


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

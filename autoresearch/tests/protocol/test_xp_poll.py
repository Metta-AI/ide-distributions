"""xp-poll (Plan 01 Task 3.9; A10, A12): evidence from completed XP requests, the review
card when an evaluation finishes, and relaunch of ``awaiting-credits`` experiments after the
credit backoff with no duplicate XP request."""

import argparse
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

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.runctx import RunContext
from autoresearch.runtime import xp_poll
from autoresearch.runtime.markers import relaunch_marker, review_marker
from autoresearch.softmax_tool import cli as softmax_cli
from autoresearch.softmax_tool.protocol import XpStatus
from autoresearch.softmax_tool.xp import ArtifactsIncomplete
from autoresearch.stats.ingest import EpisodeRow, ingest, pair

T0 = datetime.datetime.now(datetime.UTC)


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
    hypothesis = store.create(
        "hypothesis",
        {"statement": "S.", "prediction": "P.", "topics": [], "lever": "SK.x", "state": "proposed"},
        "H.\n",
    )
    walk = ("built", "uploaded", "evaluating")
    experiment = evaluating_experiment(store, hypothesis, walk[: walk.index(status) + 1])
    # Experiments run only with a current noise record (C10), so their cards are admitted.
    noise = host.canonical / "stats" / "metric-noise.yaml"
    noise.parent.mkdir(exist_ok=True)
    noise.write_text(f"game_version: '{GAME_VERSION}'\nmetrics: {{}}\n")
    gitops.commit_all(host.canonical, f"experiment {experiment} {status}")
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
    marker = f"[{review_marker(experiment)}]"
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
    kept, report = ingest(rows, DESIGN)
    assert report.kept == len(rows)
    assert len(pair(kept, DESIGN, CANDIDATE_PV)) == len(DESIGN.seeds)
    [review] = _review_requests(host, experiment)
    assert review["spec"]["refs"] == [f"ar://experiment/{experiment}"]
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


# --- awaiting-credits: relaunch after the backoff, no duplicate XP ---


def _cli_args(experiment: str) -> argparse.Namespace:
    return argparse.Namespace(experiment=experiment, batch=1, attempt=1, batches=1)


def test_credit_denial_relaunches_after_backoff_without_duplicate_xp(host, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", host.env["AUTORESEARCH_PLAYER_ID"])
    experiment = _experiment(host, "uploaded")
    meta, _ = host.store.load(experiment)
    planned = softmax_cli.xp_specs(
        meta["evaluation_design"], meta["candidate_binding"], LEAGUE, 1, 1
    )
    # The first launch (in canonical here, as after its card merged) runs out of credits.
    _set_fake_state(host, credits=2)
    launcher = RunContext(host.home, host.canonical, host.canonical, "K-launch", "r-1")
    denied = softmax_cli._xp_create(launcher, host.sm, _cli_args(experiment))
    assert denied["credit_denied"] and len(denied["created"]) == 2
    gitops.commit_all(host.canonical, "launch: credit denied")
    meta, _ = host.store.load(experiment)
    assert meta["status"] == "awaiting-credits"
    backoff_end = datetime.datetime.fromisoformat(meta["lifecycle"]["next_launch_after"])

    # Before the backoff ends: evidence for the partial batch, but no relaunch.
    early = _poll(host, backoff_end - datetime.timedelta(minutes=5))
    assert early.launches == [] and len(early.evidence) == 2
    # After it, still no credits (the fake can read them): no relaunch.
    assert _poll(host, backoff_end + datetime.timedelta(minutes=1)).launches == []

    _set_fake_state(host, credits=100)
    due = _poll(host, backoff_end + datetime.timedelta(minutes=2))
    [launch] = due.launches
    assert f"[{relaunch_marker(experiment, 1, 1)}]" in launch.why
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

"""``report baseline``: performance and cost since a date (runtime.baseline)."""

import datetime
import json

from support.state_db import add_session, record_session_start
from ulid import ULID

from autoresearch import cli
from autoresearch.io.docs import write_doc, write_json
from autoresearch.runtime import baseline
from autoresearch.runtime.handoff import handoff_path
from runtime.workspace import make_spec

UTC = datetime.UTC
NOW = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SINCE = datetime.date(2026, 10, 8)
CREDENTIAL = "cga_secret_credential_value"
OPENROUTER_KEY = "sk-or-secret-key-value"
KEY = {"limit": 500, "usage": 12.5, "limit_remaining": 487.5, "usage_daily": 4.0}
ENV = {"OPENROUTER_API_KEY": OPENROUTER_KEY, "SOFTMAX_PLAYER_CREDENTIAL": CREDENTIAL}


def at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def ts(day: int, hour: int, minute: int = 0) -> float:
    return at(day, hour, minute).timestamp()


def record_id(prefix: str, when: datetime.datetime) -> str:
    return f"{prefix}-{ULID.from_datetime(when)}"


def http_get(url, headers):
    assert url.endswith("/api/v1/key") and headers == {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    return json.dumps({"data": KEY})


def finished_card(canonical, queue, refs):
    """One card with two runs: the first requeued, the second done."""
    task = queue.create_unassigned(make_spec(mode="experimentation", refs=refs), "q", canonical.root)
    queue.assign(task, "worker")
    first = queue.claim_next("worker").runs[-1].run_id
    queue.requeue(task, "worker died")
    second = queue.claim_next("worker").runs[-1].run_id
    queue.finish(task, second, "done")
    return task, first, second


def seed(ctx, canonical, queue):
    home = ctx.hermes_home
    home.mkdir()
    experiment = record_id("E", at(8, 9))
    version = record_id("V", at(9, 9, 5))
    task, first, second = finished_card(canonical, queue, [f"ar://experiment/{experiment}"])
    kimi = "moonshotai/kimi-k3"
    # The card: run 1 on day 1 (a root and its compression continuation), run 2 on day 2.
    add_session(home, "run1", 1.0, started_at=ts(8, 10), ended_at=ts(8, 10, 5), model=kimi,
                tokens=(100, 10, 1000, 0))
    add_session(home, "run1b", 0.5, parent="run1", started_at=ts(8, 10, 5),
                ended_at=ts(8, 10, 15), model=kimi, tokens=(50, 5, 500, 0))
    add_session(home, "run2", 2.0, source="cli", started_at=ts(9, 9), ended_at=ts(9, 9, 10),
                model=kimi, tokens=(200, 20, 2000, 0))
    record_session_start(canonical.root, task, first, "run1")
    record_session_start(canonical.root, task, second, "run2")
    # The plugin may also record the continuation (a session_end): it is not another run.
    record_session_start(canonical.root, task, first, "run1b")
    add_session(home, "chat", 0.25, source="api_server", started_at=ts(9, 8))
    add_session(home, "cron", 0.1, source="cron", started_at=ts(8, 6))
    add_session(home, "delegate", 0.05, source="delegate", started_at=ts(9, 9, 30))
    add_session(home, "before", 9.0, source="cli", started_at=ts(7, 12))
    compiler_home = home / "gota-compiler" / "abcdef12-1-x1y2"
    compiler_home.mkdir(parents=True)
    add_session(compiler_home, "compile", 0.75, source="cli", started_at=ts(9, 9, 2))
    # The card's handoff: done on day 2, created the version.
    write_json(
        handoff_path(canonical.root, task, second),
        {"card": task, "run_id": second, "mode": "experimentation", "outcome": "done",
         "refs": [f"ar://experiment/{experiment}"], "report": {"created": [version]},
         "history": [{"state": "committed", "at": "2026-10-09T09:11:00+00:00"}]},
    )
    # The experiment: two XP requests of one episode each, evidence on day 2.
    layout = canonical.layout
    write_doc(
        layout.experiments / f"{experiment}.md",
        {"id": experiment, "hypothesis": canonical.hypothesis_id, "baseline": "pv_base",
         "change": {"components": ["x"], "description": "y"},
         "environment": {"game_id": "fakegame", "game_version": "65"}, "status": "evaluating",
         "xp_requests": [{"batch": 1, "attempt": 1, "request_id": "xr_1"},
                         {"batch": 1, "attempt": 1, "request_id": "xr_2"}]},
        "",
    )
    for number, fetched in ((1, at(9, 10)), (2, at(9, 11))):
        rows = f"evidence/xp/{experiment}/xr_{number}/rows.jsonl"
        (canonical.root / rows).parent.mkdir(parents=True)
        (canonical.root / rows).write_text(json.dumps({"episode_id": f"xr_{number}:0"}) + "\n")
        evidence = record_id("EV", fetched)
        write_json(
            layout.evidence_index / f"{evidence}.json",
            {"id": evidence, "kind": "xp-result", "fetched_at": fetched.isoformat(),
             "source_ids": {"xreq": f"xr_{number}", "experiment": experiment},
             "rows_path": rows},
        )
    write_json(
        layout.versions / version / "version.json",
        {"id": version, "experiment": experiment,
         "gates": {"G1": {"passed": True, "seconds": 12.5}, "G2": {"passed": True}},
         "build": {"started": "2026-10-09T09:01:00+00:00", "finished": "2026-10-09T09:04:30+00:00"}},
    )
    return task, experiment, version


def test_the_baseline_report(ctx, canonical, queue):
    task, experiment, version = seed(ctx, canonical, queue)

    result = baseline.build(ctx, ENV, SINCE, http_get, queue, NOW)
    data = result["data"]

    assert data["cards"] == [
        {"task": task, "mode": "experimentation", "model": "moonshotai/kimi-k3", "runs": 2,
         "wall_s": 900.0 + 600.0, "input_tokens": 350, "output_tokens": 35,
         "cache_read_tokens": 3500, "estimated_usd": 3.5, "outcome": "done"}
    ]
    assert data["days"] == [
        {"date": "2026-10-08", "key_usage_delta_usd": None, "cards_finished": 0,
         "estimated_usd_by_source": {"cli": 1.5, "api_server": 0.0, "compiler": 0.0,
                                     "cron": 0.1}},
        {"date": "2026-10-09", "key_usage_delta_usd": 4.0, "cards_finished": 1,
         "estimated_usd_by_source": {"cli": 2.0, "api_server": 0.25, "compiler": 0.75,
                                     "cron": 0.0, "delegate": 0.05}},
    ]
    evidence_wall = (at(9, 11) - at(8, 9)).total_seconds()
    assert data["experiments"] == [
        {"id": experiment, "xp_requests": 2, "episodes": 2, "credits": None,
         "wall_s_to_evidence": evidence_wall, "estimated_usd_of_its_cards": 3.5}
    ]
    assert data["builds"] == [
        {"card": task, "version": version, "wall_s": 210.0, "gates": {"g1": 12.5, "g2": None}}
    ]
    assert data["key"] == {"limit_usd": 500.0, "usage_usd": 12.5, "limit_remaining_usd": 487.5}
    assert result["warnings"] == []
    text = json.dumps(result)
    assert OPENROUTER_KEY not in text and CREDENTIAL not in text


def test_nothing_before_since_is_reported(ctx, canonical, queue):
    seed(ctx, canonical, queue)
    data = baseline.build(ctx, ENV, datetime.date(2026, 10, 10), http_get, queue,
                          NOW + datetime.timedelta(days=1))["data"]
    assert (data["cards"], data["experiments"], data["builds"]) == ([], [], [])
    assert [day["date"] for day in data["days"]] == ["2026-10-10"]


def test_a_fresh_agent_and_no_key(ctx, queue):
    ctx.hermes_home.mkdir()
    result = baseline.build(ctx, {}, datetime.date(2026, 10, 9), http_get, queue, NOW)
    assert result["data"]["days"] == [
        {"date": "2026-10-09", "key_usage_delta_usd": None, "cards_finished": 0,
         "estimated_usd_by_source": {"cli": 0.0, "api_server": 0.0, "compiler": 0.0, "cron": 0.0}}
    ]
    assert result["data"]["key"] == {"limit_usd": None, "usage_usd": None,
                                     "limit_remaining_usd": None}
    assert any("OPENROUTER_API_KEY" in warning for warning in result["warnings"])


def test_cli_prints_the_envelope(ctx, canonical, queue, monkeypatch, capsys):
    ctx.hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(ctx.hermes_home))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(ctx.canonical))
    monkeypatch.setenv("AUTORESEARCH_QUEUE", "file")
    monkeypatch.setenv("AUTORESEARCH_SKIP_OPENROUTER_CHECK", "1")
    today = datetime.datetime.now(UTC).date().isoformat()
    assert cli.main(["report", "baseline", "--since", today, "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert set(printed["data"]) == {"cards", "days", "experiments", "builds", "key"}
    assert cli.main(["report", "baseline", "--since", "last week"]) == 2
    assert "--since must be a date" in json.loads(capsys.readouterr().out)["error"]["message"]

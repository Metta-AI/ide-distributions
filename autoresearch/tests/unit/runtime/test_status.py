"""``status``: the read-only snapshot for the coach chat and operators (runtime.status)."""

import datetime
import json

import pytest
from support.state_db import add_session

from autoresearch import cli
from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import publish_message
from autoresearch.io.docs import write_json
from autoresearch.runtime import promotion, status
from autoresearch.runtime.ledger import commit
from autoresearch.runtime.pause import pause
from autoresearch.runtime.promotion import ChampionConflict, LeagueCheck
from runtime.workspace import make_spec

NOW = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=datetime.UTC)
CREDENTIAL = "cga_secret_credential_value"
OPENROUTER_KEY = "sk-or-secret-key-value"
KEY_RESPONSE = json.dumps({"data": {"limit": 500, "usage": 12.5, "limit_remaining": 487.5}})
ENV = {"OPENROUTER_API_KEY": OPENROUTER_KEY, "SOFTMAX_PLAYER_CREDENTIAL": CREDENTIAL}


def http_get(url, headers):
    assert url.endswith("/api/v1/key") and headers == {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    return KEY_RESPONSE


def build(ctx, queue, env=ENV, get=http_get, league_check=lambda now, grace: LeagueCheck(None, None, None, None)):
    ctx.hermes_home.mkdir(exist_ok=True)
    return status.build(ctx, env, get, queue, league_check, NOW)


def all_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from all_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from all_values(value)
    else:
        yield node


def test_a_full_snapshot(ctx, canonical, queue):
    ledger = canonical.layout.ledger
    pause(queue, ledger, "identity failure")
    write_json(ledger / "started.json", {"started_at": "2026-10-09T00:00:00Z", "by": "x"})
    write_json(ledger / "coach-hold.json", {"held_at": "2026-10-09T01:00:00Z", "by": "coach:amy"})
    commit(canonical.root, [ledger / "started.json"], "started")
    ctx.hermes_home.mkdir()
    add_session(ctx.hermes_home, "s1", 0.40, started_at=NOW.timestamp() - 60)
    (ctx.hermes_home / "distribution.yaml").write_text("name: autoresearch\nversion: 0.7.1\n")
    (ctx.hermes_home / ".softmax-distribution-commit").write_text("abc123\n")
    (ctx.hermes_home / ".softmax-update-failed").write_text(
        json.dumps({"commit": "def456", "error": "pip failed"})
    )
    task = queue.create_unassigned(make_spec(title="Find why"), "card/q", canonical.root)
    queue.assign(task, "worker")
    queue.claim_next("worker")
    keeper_log = canonical.root / ".supervisor" / "runtime" / "keeper.log"
    keeper_log.parent.mkdir(parents=True, exist_ok=True)
    keeper_log.write_text(
        "2026-10-09T11:00:00+00:00 old\n2026-10-09T11:59:00+00:00 pass done\n"
    )
    env = ENV | {"AUTORESEARCH_AUTONOMY": "on-request"}

    result = build(ctx, queue, env, league_check=lambda now, grace: LeagueCheck("pv_1", None, None, None))
    data = result["data"]

    assert data["distribution"] == {
        "version": "0.7.1",
        "commit": "abc123",
        "update_failed": {"commit": "def456", "error": "pip failed"},
    }
    assert data["paused"] is True and data["pause_reasons"] == ["identity failure"]
    assert data["coach_hold"] is True
    assert (data["autonomy"], data["started"]) == ("on-request", True)
    assert data["spend"]["today_usd"] == pytest.approx(0.40)
    assert data["spend"]["daily_budget_usd"] > 0
    assert data["key"] == {"limit_usd": 500.0, "usage_usd": 12.5, "limit_remaining_usd": 487.5}
    assert data["openrouter_floor_usd"] == 10.0
    assert data["running_cards"] == [{"task": task, "mode": "investigation", "title": "Find why"}]
    assert data["champion"] == {"policy": "pv_1", "conflict": False}
    assert data["last_keeper_run"] == "2026-10-09T11:59:00+00:00"
    assert result["warnings"] == []


def test_no_credential_appears_in_the_output(ctx, queue):
    result = build(ctx, queue)
    values = {str(value) for value in all_values(result)}
    assert CREDENTIAL not in values and OPENROUTER_KEY not in values
    assert CREDENTIAL not in json.dumps(result) and OPENROUTER_KEY not in json.dumps(result)


def test_a_fresh_agent(ctx, queue):
    data = build(ctx, queue)["data"]
    assert data["paused"] is False and data["pause_reasons"] == []
    assert data["coach_hold"] is False and data["started"] is False
    assert data["autonomy"] == "always"
    assert data["distribution"]["commit"] is None and data["distribution"]["update_failed"] is None
    assert data["running_cards"] == [] and data["open_questions"] == 0
    assert data["last_keeper_run"] is None
    assert data["spend"]["today_usd"] == 0.0


def test_unreadable_sources_become_warnings(ctx, queue):
    def broken_get(url, headers):
        raise OSError("network down")

    def broken_champion(now, grace):
        raise RuntimeError("league unreachable")

    result = build(ctx, queue, ENV, broken_get, broken_champion)
    assert result["data"]["key"] == {
        "limit_usd": None, "usage_usd": None, "limit_remaining_usd": None
    }
    assert result["data"]["champion"] == {"policy": None, "conflict": False}
    text = " ".join(result["warnings"])
    assert "OpenRouter credit unavailable" in text and "champion unavailable" in text
    assert OPENROUTER_KEY not in text


def test_a_malformed_update_failed_marker_is_reported(ctx, queue):
    ctx.hermes_home.mkdir()
    (ctx.hermes_home / ".softmax-update-failed").write_text("pip exploded")
    result = build(ctx, queue)
    assert result["data"]["distribution"]["update_failed"] == {
        "commit": "unknown", "error": "pip exploded"
    }
    assert any(".softmax-update-failed" in warning for warning in result["warnings"])


def test_open_questions_count_unanswered_published_requests(ctx, canonical, queue):
    message = runtime_messages.daily_budget_reached(30.0, 25.0, "2026-10-09")
    publish_message(canonical.root, message, allow_no_remote=True, expects_response=True)
    assert build(ctx, queue)["data"]["open_questions"] == 1


def test_cli_prints_the_envelope(ctx, canonical, queue, monkeypatch, capsys):
    ctx.hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(ctx.hermes_home))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(ctx.canonical))
    monkeypatch.setenv("AUTORESEARCH_QUEUE", "file")
    monkeypatch.setenv("AUTORESEARCH_SKIP_OPENROUTER_CHECK", "1")
    monkeypatch.setenv("AUTORESEARCH_SOFTMAX", "fake")
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_PLAYER_ID", "ply_a")
    assert cli.main(["status", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True and set(printed) == {"ok", "data", "warnings"}
    assert printed["data"]["champion"] == {"policy": None, "conflict": False}


def test_a_conflict_in_the_league_check_is_reported(ctx, queue):
    conflict = ChampionConflict("pv_1", "pv_2", "hand-made")
    data = build(ctx, queue, league_check=lambda now, grace: LeagueCheck("pv_2", None, None, conflict))
    assert data["data"]["champion"] == {"policy": "pv_2", "conflict": True}


def test_the_grace_comes_from_the_environment(ctx, queue):
    seen = []

    def check(now, grace):
        seen.append(grace)
        return LeagueCheck(None, None, None, None)

    build(ctx, queue, ENV | {"AUTORESEARCH_CHAMPION_GRACE_MINUTES": "5"}, league_check=check)
    result = build(ctx, queue, ENV | {"AUTORESEARCH_CHAMPION_GRACE_MINUTES": "bad"}, league_check=check)
    assert seen == [datetime.timedelta(minutes=5), promotion.DEFAULT_CHAMPION_GRACE]
    assert any("AUTORESEARCH_CHAMPION_GRACE_MINUTES" in warning for warning in result["warnings"])


def test_a_malformed_distribution_yaml_degrades_to_a_warning(ctx, queue):
    ctx.hermes_home.mkdir()
    (ctx.hermes_home / "distribution.yaml").write_bytes(b"\xff\xfe not text: [")
    result = build(ctx, queue)
    assert result["data"]["distribution"]["version"]
    assert any("distribution.yaml" in warning for warning in result["warnings"])


def test_a_raising_queue_gives_no_running_cards_and_a_warning(ctx, queue):
    class BrokenQueue:
        def list(self, state=None):
            raise RuntimeError("board unreachable")

    result = build(ctx, BrokenQueue())
    assert result["data"]["running_cards"] == []
    assert any("running cards unavailable" in warning for warning in result["warnings"])

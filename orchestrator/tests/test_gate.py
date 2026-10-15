import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import add_wait

from orchestrator.gate import evaluate
from orchestrator.storage import ack, open_storage, snapshot

QUIET_MINUTE = datetime(2026, 10, 15, 12, 1, tzinfo=UTC)


class Platform:
    def __init__(self, *, xp=None, credits=0, answer=None, conflict=False, in_flight=None):
        self.xp = xp or []
        self.balance = credits
        self.answer = answer
        self.conflict = conflict
        self.in_flight = in_flight

    def xp_status(self, experiment):
        assert experiment == "E-1"
        return self.xp

    def credits(self):
        return {"available": True, "credits": self.balance}

    def permit(self, ask):
        assert ask == "ask-1"
        return {"body": {"kind": "permit", "answer": self.answer}}

    def call(self, *args):
        if args == ("records", "summary"):
            return {"champion": None}
        assert args == ("promotion", "status")
        return {"conflict": self.conflict, "in_flight": self.in_flight, "champion": None}


def test_quiet_and_closed_card_comment_survive_observation(context, board_file):
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}
    with sqlite3.connect(board_file) as conn:
        conn.execute("INSERT INTO task_comments VALUES (1, 'card-1', 'question: why?')")
        conn.execute("INSERT INTO task_events VALUES (1, 'card-1', 'completed')")
    first = evaluate(context, Platform(), Platform(), QUIET_MINUTE)
    assert first["wakeAgent"] is True
    assert len(first["context"]["events"]) == 2
    assert snapshot(context)["events"] == first["context"]["events"]
    with closing(open_storage(context.storage_path)) as store:
        ack(store, first["context"]["events"])
        ack(store, first["context"]["events"])
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}


@pytest.mark.parametrize(
    ("awaiting", "platform", "kind"),
    [
        (
            {"xp": ["xp-1"]},
            Platform(xp=[{"request_id": "xp-1", "state": "completed"}]),
            "xp_terminal",
        ),
        ({"credits": True}, Platform(credits=30), "credits_available"),
        ({"permit": "ask-1"}, Platform(answer={"yes": False, "at": "now"}), "permit_answered"),
    ],
)
def test_platform_waits_wake_then_ack(context, board_file, awaiting, platform, kind):
    add_wait(board_file, awaiting, {"id": "E-1"})
    result = evaluate(context, platform, platform, QUIET_MINUTE)
    assert result["wakeAgent"] is True
    event = result["context"]["pending"][0]
    assert event["kind"] == kind
    with closing(open_storage(context.storage_path)) as store:
        ack(store, [event])
    assert evaluate(context, platform, platform, QUIET_MINUTE) == {"wakeAgent": False}


@pytest.mark.parametrize("awaiting", [{"xp": ["xp-1"]}, {"credits": True}, {"permit": "ask-1"}])
def test_unresolved_wait_does_not_wake(context, board_file, awaiting):
    add_wait(board_file, awaiting, {"id": "E-1"})
    platform = Platform(xp=[{"request_id": "xp-1", "state": "running"}])
    assert evaluate(context, platform, platform, QUIET_MINUTE) == {"wakeAgent": False}


def test_champion_conflict_and_inflight_on_tenth_minute(context, board_file):
    now = QUIET_MINUTE.replace(minute=10)
    for platform in (Platform(conflict=True), Platform(in_flight={"stage": "submitted"})):
        result = evaluate(context, platform, platform, now)
        assert result["context"]["pending"][0]["kind"] == "promotion"
        assert evaluate(context, platform, platform, QUIET_MINUTE) == {"wakeAgent": False}
    assert evaluate(context, Platform(), Platform(), now) == {"wakeAgent": False}


def test_gate_process_with_sanitized_passthrough(shared_fixture, board_file):
    # Keep only the scheduler's environment contract plus import locations for this test fixture.
    source = Path(__file__).resolve().parents[1] / "src"
    env = {
        **shared_fixture.env,
        "PYTHONPATH": os.pathsep.join([str(source), shared_fixture.env["PYTHONPATH"]]),
    }
    result = subprocess.run(
        [sys.executable, "-m", "orchestrator.gate"],
        env=env,
        cwd=shared_fixture.home,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout.splitlines()[-1]) == {"wakeAgent": False}


def test_finished_promotion_wakes_even_when_inflight_clears(context, board_file):
    class Recovered(Platform):
        def __init__(self):
            super().__init__()
            self.champion = "old"

        def call(self, *args):
            if args == ("records", "summary"):
                return {"champion": {"version": self.champion}}
            self.champion = "new"
            return {"conflict": False, "in_flight": None, "champion": {"version": "new"}}

    platform = Recovered()
    result = evaluate(context, platform, platform, QUIET_MINUTE.replace(minute=10))
    assert result["wakeAgent"]
    assert result["context"]["pending"][0]["status"]["champion"] == {"version": "new"}

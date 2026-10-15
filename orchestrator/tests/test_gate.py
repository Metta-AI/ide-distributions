import json
import shutil
import sqlite3
import subprocess
import sys
import venv
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import add_wait

from orchestrator.gate import evaluate
from orchestrator.storage import ack, open_storage, snapshot

QUIET_MINUTE = datetime(2026, 10, 15, 12, 1, tzinfo=UTC)


class Platform:
    def __init__(
        self, *, xp=None, credits=0, answer=None, conflict=None, in_flight=None, paused=False
    ):
        self.xp = xp or []
        self.balance = credits
        self.answer = answer
        self.conflict = conflict
        self.in_flight = in_flight
        self.paused = paused

    def control_read(self):
        return {"paused": self.paused}

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
        conn.execute(
            "INSERT INTO task_comments VALUES (1, 'card-1', 'researcher-1', 'question: why?')"
        )
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


def test_unchanged_promotion_waits_stay_quiet_and_pausing_quiets_conflict(context, board_file):
    now = QUIET_MINUTE.replace(minute=10)
    for stage in ("submitted", "awaiting-permit"):
        platform = Platform(in_flight={"stage": stage})
        for minute in (10, 20):
            assert evaluate(context, platform, platform, now.replace(minute=minute)) == {
                "wakeAgent": False
            }
    platform = Platform(conflict="league champion was not recorded")
    assert evaluate(context, platform, platform, QUIET_MINUTE) == {"wakeAgent": False}
    assert (
        evaluate(context, platform, platform, now)["context"]["pending"][0]["kind"] == "promotion"
    )
    platform.paused = True
    assert evaluate(context, platform, platform, now.replace(minute=20)) == {"wakeAgent": False}
    assert evaluate(context, Platform(), Platform(), now) == {"wakeAgent": False}


def test_gate_process_with_sanitized_passthrough(shared_fixture, board_file):
    # Exercise the real script/exec boundary, with no PYTHONPATH or inherited credentials.
    source = Path(__file__).resolve().parents[1]
    scripts = shared_fixture.home / "scripts"
    scripts.mkdir()
    shutil.copy(source / "scripts/supervisor_gate.py", scripts)
    shutil.copytree(
        source / "src/orchestrator",
        shared_fixture.home / "src/orchestrator",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    venv_dir = shared_fixture.home / "local/venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(venv_dir)
    site = venv_dir / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    shutil.copytree(
        source / "src/orchestrator",
        site / "orchestrator",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(shared_fixture.home / "fixture/ide_research_core", site / "ide_research_core")
    env = {k: v for k, v in shared_fixture.env.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(scripts / "supervisor_gate.py")],
        env=env,
        cwd=scripts,
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
    assert evaluate(context, platform, platform, QUIET_MINUTE.replace(minute=20)) == {
        "wakeAgent": False
    }


def test_bookkeeping_and_orchestrator_comments_never_need_ack(context, board_file):
    with sqlite3.connect(board_file) as conn:
        conn.executemany(
            "INSERT INTO task_events (task_id, kind) VALUES ('card-1', ?)",
            [
                (kind,)
                for kind in (
                    "created",
                    "claimed",
                    "spawned",
                    "worker_registered",
                    "edited",
                    "linked",
                    "attached",
                    "reprioritized",
                    "status",
                    "archived",
                    "unblocked",
                    "commented",
                )
            ],
        )
        conn.execute("INSERT INTO task_comments VALUES (1, 'card-1', 'orchestrator', 'Answer')")
    for _ in range(2):
        assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}
    with closing(open_storage(context.storage_path)) as store:
        assert store.execute("SELECT * FROM handled").fetchall() == []


@pytest.mark.parametrize(
    "kind",
    [
        "completed",
        "gave_up",
        "crashed",
        "timed_out",
        "blocked",
        "review_requested",
        "changes_requested",
        "block_loop_detected",
    ],
)
def test_native_decision_events_need_explicit_ack(context, board_file, kind):
    with sqlite3.connect(board_file) as conn:
        conn.execute("INSERT INTO task_events VALUES (1, 'card-1', ?)", (kind,))
    result = evaluate(context, Platform(), Platform(), QUIET_MINUTE)
    assert result["context"]["events"][0]["kind"] == kind
    with closing(open_storage(context.storage_path)) as store:
        ack(store, result["context"]["events"])
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}


@pytest.mark.parametrize("experiment", [None, {}, {"id": None}])
def test_bad_xp_handoff_is_reported_and_acknowledgeable(context, board_file, experiment):
    add_wait(board_file, {"xp": ["xp-1"]}, experiment)
    event = evaluate(context, Platform(), Platform(), QUIET_MINUTE)["context"]["pending"][0]
    assert event["kind"] == "bad_handoff"
    assert event["table"] == "task_runs" and event["id"] == 1
    assert event["card"] == "card-1"
    with closing(open_storage(context.storage_path)) as store:
        ack(store, [event])
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}

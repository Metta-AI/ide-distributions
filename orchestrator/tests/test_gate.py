import json
import shutil
import sqlite3
import subprocess
import sys
import venv
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import add_wait

from orchestrator.gate import evaluate

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

    def in_game(self, game):
        self.games = [*getattr(self, "games", []), game]
        return self

    def xp_status(self, experiment):
        assert experiment == "E-1"
        return self.xp

    def credits(self):
        return {"available": True, "credits": self.balance}

    def permit(self, ask):
        assert ask == "ask-1"
        return {"body": {"kind": "permit", "answer": self.answer}}

    def call(self, *args):
        if args == ("promotion", "pending"):
            return self.in_flight
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
        conn.execute("INSERT INTO task_events (id, task_id, kind) VALUES (1, 'card-1', 'completed')")
    first = evaluate(context, Platform(), Platform(), QUIET_MINUTE)
    assert first == {"wakeAgent": False}


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
def test_platform_waits_have_stable_queue_input(context, board_file, awaiting, platform, kind):
    add_wait(board_file, awaiting, {"id": "E-1"})
    result = evaluate(context, platform, platform, QUIET_MINUTE)
    assert result["wakeAgent"] is True
    event = result["context"]["pending"][0]
    assert event["kind"] == kind
    assert evaluate(context, platform, platform, QUIET_MINUTE) == result


@pytest.mark.parametrize("awaiting", [{"xp": ["xp-1"]}, {"credits": True}, {"permit": "ask-1"}])
def test_unresolved_wait_does_not_wake(context, board_file, awaiting):
    add_wait(board_file, awaiting, {"id": "E-1"})
    platform = Platform(xp=[{"request_id": "xp-1", "state": "running"}])
    assert evaluate(context, platform, platform, QUIET_MINUTE) == {"wakeAgent": False}


def test_cron_only_requests_coordinator_review_of_pending_promotion(context, board_file):
    platform = Platform(in_flight={"stage": "submitted"})
    now = QUIET_MINUTE.replace(minute=10)
    result = evaluate(context, platform, platform, now)
    assert result["context"]["pending"] == [{"kind": "promotion_poll", "tick": now.isoformat()}]
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


def test_bookkeeping_and_comments_do_not_wake_cron(context, board_file):
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
def test_native_decision_events_are_not_requeued_by_cron(context, board_file, kind):
    with sqlite3.connect(board_file) as conn:
        conn.execute("INSERT INTO task_events (id, task_id, kind) VALUES (1, 'card-1', ?)", (kind,))
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE) == {"wakeAgent": False}


@pytest.mark.parametrize("experiment", [None, {}, {"id": None}])
def test_bad_xp_handoff_has_stable_queue_input(context, board_file, experiment):
    add_wait(board_file, {"xp": ["xp-1"]}, experiment)
    event = evaluate(context, Platform(), Platform(), QUIET_MINUTE)["context"]["pending"][0]
    assert event["kind"] == "bad_handoff"
    assert event["table"] == "task_runs" and event["id"] == 1
    assert event["card"] == "card-1"
    assert evaluate(context, Platform(), Platform(), QUIET_MINUTE)["context"]["pending"] == [event]


def test_a_card_of_another_game_waits_in_that_game(context, board_file):
    add_wait(board_file, {"xp": ["xp-1"]}, {"id": "E-1"})
    with sqlite3.connect(board_file) as conn:
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload) VALUES ('card-1', "
            "'creation_command', ?)",
            (json.dumps({"assignee": "researcher-1", "game": "newgame"}),),
        )
    platform = Platform(xp=[{"request_id": "xp-1", "state": "completed"}])
    result = evaluate(context, platform, platform, QUIET_MINUTE)
    assert result["context"]["pending"][0]["game"] == "newgame"
    assert platform.games == ["newgame"]


def test_every_game_with_a_workspace_is_polled_for_promotion(context, board_file):
    host = context.home.parent.parent
    for game in ("gota", "newgame"):
        (host / f"canonical-{game}.git").mkdir()
        (host / f"canonical-{game}.git" / "HEAD").write_text("ref: refs/heads/main\n")
    context.env.update(RESEARCH_GAME="gota", RESEARCH_CANONICAL=str(host / "canonical-gota.git"))
    platform = Platform(in_flight={"stage": "submitted"})
    now = QUIET_MINUTE.replace(minute=10)
    pending = evaluate(context, platform, platform, now)["context"]["pending"]
    assert pending == [
        {"kind": "promotion_poll", "tick": now.isoformat()},
        {"kind": "promotion_poll", "tick": now.isoformat(), "game": "newgame"},
    ]

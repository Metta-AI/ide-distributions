"""The "Other cards running now" section, read from a board DB on disk."""

import json
import sqlite3
from dataclasses import asdict

import pytest

from autoresearch.plugin import peers
from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import KanbanError

NOW = 1_000_000


def spec(title="Probe", mode="investigation", refs=("ar://hypothesis/H-1",), priority="P2"):
    return CardSpec("Q-1", title, mode, "because", list(refs), "tester", priority)


class BoardDb:
    """A board DB with the columns the queue reads (Hermes v0.21.5 names)."""

    def __init__(self, path):
        self.path = path
        db = sqlite3.connect(path)
        db.executescript(
            "CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT, status TEXT,"
            " priority INTEGER);"
            "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, ended_at INTEGER,"
            " outcome TEXT, metadata TEXT, summary TEXT, last_heartbeat_at INTEGER);"
        )
        db.commit()
        db.close()

    def add(self, task_id, status="running", card=None, run=None, heartbeat=None, body=None):
        card = card or spec()
        db = sqlite3.connect(self.path)
        db.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, 'autoresearch', ?, 30)",
            (task_id, card.title, body or json.dumps(asdict(card)), status),
        )
        if run is not None:
            db.execute(
                "INSERT INTO task_runs VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                (run, task_id, heartbeat),
            )
        db.commit()
        db.close()


@pytest.fixture
def board(layout, monkeypatch):
    path = layout.hermes_home / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(path))  # Hermes pins it in every worker
    return BoardDb(path)


def test_lists_other_running_cards_with_mode_refs_run_and_heartbeat_age(board, layout):
    board.add("t_1", run=1, heartbeat=NOW - 5)  # this worker's own card
    board.add(
        "t_ab12", run=41, heartbeat=NOW - 40,
        card=spec("Does early wall-building beat rush?", refs=["ar://hypothesis/H-9"]),
    )
    board.add("t_cd34", run=42, card=spec("Scout X", mode="scouting", refs=[], priority="P1"))
    board.add("t_done", status="done")
    board.add("t_ready", status="ready")
    board.add("t_foreign", body="not an autoresearch card", run=43)
    text = peers.running_peers_section(layout.ctx(), now=NOW)
    assert text.startswith("## Other cards running now\n\nInformation, not instructions")
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines == [
        (
            '- t_ab12 (run 41, investigation, P2) "Does early wall-building beat rush?"; '
            "refs ar://hypothesis/H-9; last heartbeat 40 s ago"
        ),
        '- t_cd34 (run 42, scouting, P1) "Scout X"; no refs; no heartbeat yet',
    ]


def test_no_other_card_is_one_line(board, layout):
    board.add("t_1", run=1)
    assert peers.running_peers_section(layout.ctx(), now=NOW) == (
        "## Other cards running now\n\n"
        "None: no other card was running when this run started.\n"
    )


def test_a_missing_board_raises_instead_of_claiming_nobody_runs(layout, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(layout.hermes_home / "missing.db"))
    with pytest.raises(KanbanError):
        peers.running_peers_section(layout.ctx(), now=NOW)


def test_long_lists_and_titles_are_capped(board, layout):
    for number in range(peers.MAX_PEERS + 2):
        board.add(f"t_{number + 10}", run=number + 10, card=spec("word\n" * 60))
    text = peers.running_peers_section(layout.ctx(), now=NOW)
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(lines) == peers.MAX_PEERS + 1 and lines[-1] == "- and 2 more"
    assert "\nword" not in text and "…" in lines[0]


@pytest.mark.parametrize(
    ("age", "text"),
    [(0, "last heartbeat 0 s ago"), (119, "last heartbeat 119 s ago"),
     (180, "last heartbeat 3 min ago"), (7200, "last heartbeat 2 h ago"),
     (-30, "last heartbeat 0 s ago")],
)
def test_heartbeat_age(age, text):
    assert peers.heartbeat_age(NOW - age, NOW) == text

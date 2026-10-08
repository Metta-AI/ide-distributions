"""xp-poll's XP seat contract (C3 ruling): keep our seats, reduce them by the mean, one row
per episode; incomplete own-seat coverage fails the episode with a reason."""

import dataclasses

import pytest

from autoresearch.runtime.xp_poll import _rows
from autoresearch.softmax_tool.protocol import XpHandle
from autoresearch.stats.design import EvaluationDesign, MetricSpec

DESIGN = EvaluationDesign(
    "1.0",
    "base:v0",
    (0, 1, 2, 3, 4),
    ("opp:a", "opp:b", "opp:c", "opp:d", "opp:e"),
    (7,),
    (MetricSpec("glory", "up", "primary", 1.0, None),),
    0.5,
    {},
    1,
    "rev1",
)
HANDLE = XpHandle("xreq_1", "E-1", 1, 1, 7, "candidate")


def _entries(episode: int, seats=range(10)) -> list[dict]:
    """Ten-seat results like GotA: glory equals the seat number."""
    return [
        {"episode": episode, "seat": seat, "seed": 7, "status": "completed",
         "metrics": {"glory": seat, "win_rate": 1}}
        for seat in seats
    ]


def _map(entries, design=DESIGN, label=None):
    return _rows({"seed": 7}, entries, HANDLE, design, "cand:v1", "1.0", label)


def test_ten_seats_reduce_to_one_row_of_our_seats_mean():
    rows = _map(_entries(0) + _entries(1))

    assert len(rows) == 2
    assert [row.episode_id for row in rows] == ["xreq_1:0", "xreq_1:1"]
    for row in rows:
        assert row.status == "completed" and row.reason is None
        assert row.metrics == {"glory": 2.0, "win_rate": 1.0}  # mean of seats 0-4, not 0-9
        assert row.policy_version == "cand:v1" and row.seed == 7
        assert row.seats == DESIGN.seats and row.opponents == DESIGN.opponents


def test_missing_own_seat_fails_the_episode_with_a_reason():
    [row] = _map(_entries(0, seats=[0, 1, 2, 4, 5, 6, 7, 8, 9]))

    assert row.status == "failed" and row.metrics == {}
    assert "[3]" in row.reason


def test_failed_own_seat_fails_the_episode_but_a_failed_opponent_does_not():
    entries = _entries(0)
    entries[2]["status"] = "failed"
    [row] = _map(entries)
    assert row.status == "failed" and "[2]" in row.reason

    entries = _entries(0)
    entries[8]["status"] = "failed"
    [row] = _map(entries)
    assert row.status == "completed"


def test_entries_without_seat_break_the_contract():
    entries = _entries(0)
    del entries[0]["seat"]
    with pytest.raises(ValueError, match="'seat'"):
        _map(entries)


def test_duplicate_own_seat_is_refused():
    with pytest.raises(ValueError, match="seat 1 twice"):
        _map(_entries(0) + _entries(0, seats=[1]))


def test_several_results_files_keep_episode_ids_unique():
    first = _map(_entries(0), label="aaa")
    second = _map(_entries(0), label="bbb")
    assert {r.episode_id for r in first} | {r.episode_id for r in second} == {
        "xreq_1:aaa:0",
        "xreq_1:bbb:0",
    }


def test_design_without_seats_fails_rather_than_divides_by_zero():
    [row] = _map(_entries(0), design=dataclasses.replace(DESIGN, seats=()))
    assert row.status == "failed" and "no seats" in row.reason

"""Pins: a downloaded artifact or replay kept by (kind, ref); adding twice returns the first."""

from __future__ import annotations

import pytest

from research_core.store import pins
from research_core.store.errors import StoreError

T0 = "2026-10-28T21:04:05.123456Z"


def test_add_and_find(coworld_store, harness):
    download = {"path": "documents/replays/ep_1.json", "digest": "sha256:abc", "size": 1234}
    record = pins.add(coworld_store, harness, kind="replay", ref="episode:ep_1", download=download)
    assert record == {
        "id": 1,
        "kind": "replay",
        "ref": "episode:ep_1",
        "download": download,
        "created": T0,
        "existing": False,
    }
    found = pins.find(coworld_store, "replay", "episode:ep_1")
    assert found == {
        "id": 1,
        "kind": "replay",
        "ref": "episode:ep_1",
        "download": download,
        "created": T0,
    }
    assert pins.find(coworld_store, "replay", "episode:other") is None
    assert pins.find(coworld_store, "artifact", "episode:ep_1") is None
    # Pins are bookkeeping: no event.
    assert coworld_store.last_seq() == 0
    assert coworld_store.unmarked_changes() == []


def test_add_returns_the_existing_pin_unchanged(coworld_store, harness, clock):
    first = pins.add(coworld_store, harness, kind="replay", ref="r", download={"path": "a"})
    clock.advance(60)
    second = pins.add(coworld_store, harness, kind="replay", ref="r", download={"path": "b"})
    assert second["existing"] is True
    assert second["id"] == first["id"]
    assert second["download"] == {"path": "a"}
    assert second["created"] == T0
    other = pins.add(coworld_store, harness, kind="artifact", ref="r", download={"path": "c"})
    assert other["id"] == 2
    assert other["existing"] is False


def test_add_validates_everything_at_once(coworld_store, harness):
    with pytest.raises(StoreError) as info:
        pins.add(coworld_store, harness, kind="", ref="", download="not a dict")
    assert len(info.value.problems) == 3
    assert coworld_store.q("SELECT * FROM pins") == []

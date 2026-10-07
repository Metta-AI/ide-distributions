import datetime

import pytest

from autoresearch.io.docs import write_doc
from autoresearch.kb.compaction import compact
from autoresearch.paths import Layout
from autoresearch.records.registry import path_for
from autoresearch.records.store import RecordStore

NOW = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.UTC)


def _claim(store, stale_days=None):
    meta = {
        "kind": "mechanism",
        "status": "belief",
        "confidence": 0.5,
        "evidence": [],
        "revise_if": "x",
        "topics": [],
        "last_checked_version": "65",
    }
    if stale_days is not None:
        at = (NOW - datetime.timedelta(days=stale_days)).isoformat()
        reason = f"game version changed: 65 -> 66 ({at})"
        meta |= {
            "status": "stale",
            "status_reason": reason,
            "status_history": [{"from": "belief", "to": "stale", "reason": reason, "at": at}],
        }
    return store.create("claim", meta, "A claim.")


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path)


def _workspace(layout, total, stale_days):
    """``total`` claims; the first ones stale for ``stale_days[i]`` days, the rest fresh."""
    store = RecordStore(layout)
    stale = [_claim(store, days) for days in stale_days]
    fresh = [_claim(store) for _ in range(total - len(stale_days))]
    return store, stale, fresh


def _archived(layout):
    return sorted(p.name.split("--")[0].removesuffix(".md") for p in layout.archive.glob("*.md"))


def test_noop_below_trigger(layout):
    _workspace(layout, 100, [30] * 9)  # 9% stale
    assert compact(layout, NOW) == []
    assert _archived(layout) == []


def test_above_trigger_archives_down_to_target_longest_first(layout):
    days = [100 - i for i in range(12)]  # 12% stale, all older than 14 days
    store, stale, _ = _workspace(layout, 100, days)
    archived = compact(layout, NOW)
    # (12 - k) / (100 - k) <= 0.025  ->  k >= 9.74  ->  k = 10
    assert archived == stale[:10]
    assert _archived(layout) == sorted(stale[:10])
    meta, _ = store.load(stale[0])
    assert meta["status"] == "stale" and "compaction" in meta["archive_reason"]
    assert path_for(layout, stale[0]).parent == layout.archive
    assert len(list(layout.kb_log.glob("*.md"))) == 10
    assert compact(layout, NOW) == []


def test_game_update_storm_archives_none(layout):
    _workspace(layout, 100, [0] * 40)  # 40% stale today
    assert compact(layout, NOW) == []
    assert _archived(layout) == []


def test_young_stale_claims_are_skipped(layout):
    _, stale, _ = _workspace(layout, 100, [30, 30, 30] + [5] * 10)
    assert compact(layout, NOW) == stale[:3]


def test_cited_claims_never_archived(layout):
    _, stale, _ = _workspace(layout, 100, [60] * 12)
    hub = "HUB-01KB0000000000000000000001"
    write_doc(
        layout.hubs / f"{hub}.md",
        {"id": hub, "title": "Mechanics", "claims": [stale[0]]},
        f"Fact [[{stale[1]}]].\n",
    )
    topic = "T-01KB0000000000000000000001"
    write_doc(
        layout.topics / f"{topic}.md", {"id": topic, "status": "open", "claims": [stale[2]]}, ""
    )
    closed = "T-01KB0000000000000000000002"
    write_doc(
        layout.topics / f"{closed}.md", {"id": closed, "status": "closed", "claims": [stale[3]]}, ""
    )
    hyp = "H-01KB0000000000000000000001"
    write_doc(
        layout.hypotheses / f"{hyp}.md",
        {"id": hyp, "state": "proposed", "source": [f"ar://claim/{stale[4]}"]},
        "",
    )
    archived = compact(layout, NOW)
    assert not set(archived) & set(stale[:3] + stale[4:5])
    assert stale[3] in archived  # cited only by a closed topic


def test_pending_reverification_is_skipped(layout):
    _, stale, _ = _workspace(layout, 100, [60] * 12)
    archived = compact(layout, NOW, pending_reverify={stale[0], f"ar://claim/{stale[1]}"})
    assert stale[0] not in archived and stale[1] not in archived
    assert len(archived) == 10

"""Archive moves: a record moved from its directory to its archive directory, id unchanged."""

import datetime

import pytest

from ide_research_core.records.registry import entity_of_path, path_for
from ide_research_core.records.store import RecordProblems

from .fixtures import CLAIM, TOPIC

NOW = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.UTC)


def _rel(layout, path):
    return path.relative_to(layout.root).as_posix()


@pytest.fixture
def claim_id(store):
    return store.create("claim", CLAIM, "Damage is 10.", slug="damage")


def test_archive_moves_file_and_keeps_id(store, layout, claim_id):
    old = path_for(layout, claim_id)
    new = store.archive(claim_id, "compaction: stale 30 days", NOW)
    assert not old.exists()
    assert new == layout.archive / old.name
    assert path_for(layout, claim_id) == new
    meta, body = store.load(claim_id)
    assert meta["id"] == claim_id and meta["status"] == CLAIM["status"]
    assert meta["archive_reason"] == "compaction: stale 30 days"
    assert meta["archived_at"] == NOW.isoformat()
    assert body.strip() == "Damage is 10."
    assert store.find("claim") == [claim_id]
    assert entity_of_path(layout, _rel(layout, new)) == "claim"
    assert store.validate(claim_id) == []


def test_archive_twice_is_rejected(store, claim_id):
    store.archive(claim_id, "first", NOW)
    with pytest.raises(RecordProblems, match="already archived"):
        store.archive(claim_id, "again", NOW)


def test_archive_needs_reason(store, claim_id):
    with pytest.raises(RecordProblems, match="archive_reason"):
        store.archive(claim_id, "", NOW)


def test_archive_of_entity_without_archive_is_rejected(store):
    tid = store.create("topic", TOPIC)
    with pytest.raises(RecordProblems, match="no archive"):
        store.archive(tid, "x", NOW)


def _move(store, layout, claim_id, after_meta=None, after_body=None, new_name=None):
    old_path = path_for(layout, claim_id)
    before = store.load(claim_id)
    meta = {**before[0], "archived_at": NOW.isoformat(), "archive_reason": "compacted"}
    after = (after_meta(meta) if after_meta else meta, after_body or before[1])
    new_rel = f"kb/archive/{new_name or old_path.name}"
    return new_rel, store.validate_changes(
        [(_rel(layout, old_path), before, None), (new_rel, None, after)]
    )


def test_move_pair_is_one_legal_change(store, layout, claim_id):
    _, problems = _move(store, layout, claim_id)
    assert problems == {}


def test_move_that_edits_meta_is_rejected(store, layout, claim_id):
    rel, problems = _move(store, layout, claim_id, after_meta=lambda m: {**m, "confidence": 0.1})
    assert any("confidence" in p for p in problems[rel])


def test_move_that_edits_body_is_rejected(store, layout, claim_id):
    rel, problems = _move(store, layout, claim_id, after_body="Damage is 12.")
    assert any("body" in p for p in problems[rel])


def test_move_without_archive_fields_is_rejected(store, layout, claim_id):
    rel, problems = _move(
        store,
        layout,
        claim_id,
        after_meta=lambda m: {k: v for k, v in m.items() if k != "archive_reason"},
    )
    assert any("archive_reason" in p for p in problems[rel])


def test_move_with_status_change_is_rejected(store, layout, claim_id):
    def to_stale(meta):
        entry = {"from": "belief", "to": "stale", "reason": "game 66", "ref": None, "at": "x"}
        return {**meta, "status": "stale", "status_reason": "game 66", "status_history": [entry]}

    rel, problems = _move(store, layout, claim_id, after_meta=to_stale)
    assert any("status" in p for p in problems[rel])


def test_lone_deletion_is_still_rejected(store, layout, claim_id):
    rel = _rel(layout, path_for(layout, claim_id))
    problems = store.validate_changes([(rel, store.load(claim_id), None)])
    assert any("deleted" in p for p in problems[rel])


def test_validate_changes_passes_other_changes_through(store, layout, claim_id):
    rel = _rel(layout, path_for(layout, claim_id))
    before = store.load(claim_id)
    after = ({**before[0], "id": "C-01KB0000000000000000000099"}, before[1])
    problems = store.validate_changes([(rel, before, after), ("plan.md", None, ({}, "x"))])
    assert list(problems) == [rel]


def test_move_that_renames_the_file_is_not_a_move(store, layout, claim_id):
    _, problems = _move(store, layout, claim_id, new_name=f"{claim_id}--renamed.md")
    old_rel = _rel(layout, path_for(layout, claim_id))
    assert any("deleted" in p for p in problems[old_rel])


def test_archive_fields_are_set_once(store, claim_id):
    store.archive(claim_id, "compacted", NOW)
    with pytest.raises(RecordProblems, match="archive_reason is set once"):
        store.update(claim_id, {"archive_reason": "something else"})


def test_validate_all_flags_duplicate_id_across_claims_and_archive(store, layout, claim_id):
    path = path_for(layout, claim_id)
    layout.archive.mkdir(parents=True)
    (layout.archive / path.name).write_text(path.read_text())  # interrupted archive move
    assert path_for(layout, claim_id) == path  # the primary copy wins
    _, problems = store.validate_all()
    archived_rel = f"kb/archive/{path.name}"
    assert list(problems) == [archived_rel]
    assert "duplicate id" in problems[archived_rel][0]

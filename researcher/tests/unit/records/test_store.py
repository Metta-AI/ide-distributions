import pytest

from ide_research_core.records.registry import REGISTRY, entity_of_path, path_for
from ide_research_core.records.store import RecordNotFound

from .fixtures import CLAIM, HYP, JOURNAL, JOURNAL_BODY, RESULT, TOPIC, VERSION


# --- brief tests (verbatim, reformatted onto separate lines) ---
def test_create_validate(store, layout):
    cid = store.create("claim", CLAIM, "Damage is 10.", slug="damage")
    assert path_for(layout, cid, "damage").exists()


def test_result_body_change_rejected(store):
    rid = store.create("result", RESULT, "analysis A")
    with pytest.raises(ValueError, match="immutable"):
        store.update(rid, {}, body="analysis B")


def test_result_deletion_rejected(store, layout):
    rid = store.create("result", RESULT, "a")
    before = store.load(rid)
    assert any(
        "deleted" in e
        for e in store.validate_change(
            str(path_for(layout, rid).relative_to(layout.root)), before, None
        )
    )


def test_version_events(store):
    vid = store.create("version", VERSION)
    eid = store.add_event(vid, "uploaded", {"policy_version_id": "pv_1"})
    assert eid.startswith("VE-") and store.find("version_event", version=vid) == [eid]


def test_journal_needs_decisions(store):
    with pytest.raises(ValueError, match="Decisions"):
        store.create(
            "journal",
            {
                "iteration": 1,
                "mode": "planning",
                "card": "t_1",
                "doctrine_version": "1",
                "outcome": "done",
            },
            "none",
        )


# --- additional store tests ---
def test_no_lever_entity():
    assert "lever" not in REGISTRY


def test_create_load_roundtrip_and_layout(store, layout):
    tid = store.create("topic", TOPIC, "Why last hits matter.", slug="last-hits")
    meta, body = store.load(tid)
    assert meta["id"] == tid and meta["title"] == TOPIC["title"]
    assert body.strip() == "Why last hits matter."
    assert path_for(layout, tid).name == f"{tid}--last-hits.md"
    assert path_for(layout, tid).parent == layout.topics


def test_json_layouts(store, layout):
    vid = store.create("version", VERSION)
    assert path_for(layout, vid) == layout.versions / vid / "version.json"
    eid = store.add_event(vid, "submitted", {"league": "l1"})
    assert path_for(layout, eid) == layout.versions / vid / "events" / f"{eid}.json"
    meta, body = store.load(eid)
    assert meta["version"] == vid and meta["kind"] == "submitted" and body == ""
    evid = store.create("evidence", {"sha256": "a" * 64, "kind": "replay"})
    assert path_for(layout, evid) == layout.evidence_index / f"{evid}.json"


def _rel(layout, path):
    return str(path.relative_to(layout.root))


def test_entity_of_path(store, layout):
    cid = store.create("claim", CLAIM, "x", slug="damage")
    vid = store.create("version", VERSION)
    eid = store.add_event(vid, "uploaded", {})
    assert entity_of_path(layout, _rel(layout, path_for(layout, cid))) == "claim"
    assert entity_of_path(layout, _rel(layout, path_for(layout, vid))) == "version"
    assert entity_of_path(layout, _rel(layout, path_for(layout, eid))) == "version_event"
    assert entity_of_path(layout, "plan.md") is None
    assert entity_of_path(layout, "kb/claims/not-a-record.md") is None
    assert entity_of_path(layout, f"results/{cid}.md") is None  # wrong directory for prefix


def test_schema_violation_rejected(store):
    with pytest.raises(ValueError, match="objective"):
        store.create("topic", {k: v for k, v in TOPIC.items() if k != "objective"})


def test_id_assigned_by_store(store):
    with pytest.raises(ValueError, match="id"):
        store.create("topic", {**TOPIC, "id": "T-01K6Z3V8QX0000000000000000"})


def test_add_event_requires_existing_version(store):
    with pytest.raises(RecordNotFound):
        store.add_event("V-01K6Z3V8QX0000000000000000", "uploaded", {})


def test_find_filters(store):
    a = store.create("hypothesis", HYP)
    store.create("hypothesis", {**HYP, "lever": "other"})
    assert store.find("hypothesis", lever="SK.last_hit") == [a]
    assert len(store.find("hypothesis")) == 2


def test_hypothesis_status_records_history(store):
    hid = store.create("hypothesis", HYP)
    store.set_status(hid, "under-test", reason="E-1 registered", ref="E-1")
    meta, _ = store.load(hid)
    assert meta["state"] == "under-test" and meta["state_reason"] == "E-1 registered"
    assert [(h["from"], h["to"], h["ref"]) for h in meta["state_history"]] == [
        ("proposed", "under-test", "E-1")
    ]


def test_validate_change_checks_id_matches_path(store, layout):
    tid = store.create("topic", TOPIC)
    meta, body = store.load(tid)
    rel = str(path_for(layout, tid).relative_to(layout.root))
    other = store.create("topic", TOPIC)
    problems = store.validate_change(rel, None, ({**meta, "id": other}, body))
    assert any("does not match" in p for p in problems)


def test_validate_change_ignores_non_record_paths(store):
    assert store.validate_change("plan.md", None, ({}, "x")) == []


def test_validate_change_flags_bad_name_in_record_dir(store):
    assert store.validate_change("kb/claims/stray.md", None, ({}, "x"))


def test_journal_with_decisions_ok(store):
    jid = store.create("journal", JOURNAL, JOURNAL_BODY)
    assert jid.startswith("J-")

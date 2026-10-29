"""The field registry: one source for validation, the SOUL Records section and tool schemas."""

from __future__ import annotations

import sqlite3

import pytest

from research_core.store import _common, migrate, registry
from research_core.store.registry import RECORDS, FieldSpec, RecordSpec

REQUIRED_KINDS = {
    "goal",
    "task",
    "version",
    "claim",
    "experiment",
    "directive",
    "champion",
    "capability",
    "intent",
}
INTERNAL_COLUMNS = {"updated"}


def columns_of(kind: str, table: str) -> list[str]:
    conn = sqlite3.connect(":memory:")
    migrate.apply_migrations(conn, kind)
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_every_required_record_kind_is_registered():
    assert REQUIRED_KINDS <= set(RECORDS)
    for name, spec in RECORDS.items():
        assert isinstance(spec, RecordSpec)
        assert spec.name == name
        assert spec.description.strip()
        assert spec.fields


@pytest.mark.parametrize("name", sorted(RECORDS))
def test_every_table_column_appears_in_its_record_spec(name):
    spec = RECORDS[name]
    columns = columns_of(spec.store, spec.table)
    assert columns, f"{spec.table} is not a table of the {spec.store} store"
    stored = {f.name for f in spec.fields if not f.computed}
    for column in columns:
        if column in INTERNAL_COLUMNS or column.startswith("lease_"):
            continue
        assert column in stored, f"{name}: column {column} has no FieldSpec"


@pytest.mark.parametrize("name", sorted(RECORDS))
def test_stored_fields_are_columns_and_computed_fields_are_not(name):
    spec = RECORDS[name]
    columns = set(columns_of(spec.store, spec.table))
    for field in spec.fields:
        if field.computed:
            assert field.name not in columns, f"{name}.{field.name} is computed but is a column"
        else:
            assert field.name in columns, f"{name}.{field.name} is not a column of {spec.table}"


@pytest.mark.parametrize("name", sorted(RECORDS))
def test_field_specs_are_well_formed(name):
    spec = RECORDS[name]
    names = [f.name for f in spec.fields]
    assert len(names) == len(set(names))
    for field in spec.fields:
        assert isinstance(field, FieldSpec)
        assert field.type in registry.FIELD_TYPES, (name, field.name, field.type)
        assert field.description.strip().endswith("."), (name, field.name)
        assert len(field.description.split()) <= 40, (name, field.name)
        if field.type == "enum":
            assert field.enum, (name, field.name)
        else:
            assert field.enum is None, (name, field.name)
        if field.computed:
            assert not field.required


def test_status_fields_and_transitions_agree():
    for name, spec in RECORDS.items():
        status = spec.status_field
        if spec.statuses:
            assert status is not None, name
            assert status.type == "enum" and status.enum == spec.statuses, name
        for source, targets in spec.transitions.items():
            assert source in spec.statuses, (name, source)
            for target in targets:
                assert target in spec.statuses, (name, source, target)
                assert target != source


def test_locked_transitions():
    goal = RECORDS["goal"]
    assert goal.transitions == {
        "active": ("parked", "completed", "abandoned"),
        "parked": ("active", "abandoned"),
    }
    task = RECORDS["task"]
    assert task.can_transition("proposed", "unclaimed")
    assert task.can_transition("unclaimed", "in-progress")
    assert task.can_transition("in-progress", "complete")
    assert task.can_transition("in-progress", "unclaimed")  # release
    assert task.can_transition("complete", "in-progress")  # reopen
    for open_status in ("proposed", "unclaimed", "in-progress"):
        assert task.can_transition(open_status, "abandoned")
    assert not task.can_transition("complete", "abandoned")
    assert not task.can_transition("proposed", "in-progress")
    assert not task.can_transition("abandoned", "unclaimed")
    experiment = RECORDS["experiment"]
    assert experiment.transitions == {
        "proposed": ("running", "abandoned"),
        "running": ("confirmed", "refuted", "inconclusive", "abandoned"),
    }
    assert RECORDS["claim"].transitions == {"active": ("superseded", "refuted")}
    assert RECORDS["version"].transitions == {} and RECORDS["version"].statuses == ()
    assert RECORDS["directive"].transitions == {"active": ("retired",)}
    assert RECORDS["intent"].transitions == {"pending": ("done", "failed")}


def test_prefixes_are_unique_and_match_the_locked_identifiers():
    prefixed = {spec.prefix: name for name, spec in RECORDS.items() if spec.prefix}
    assert len(prefixed) == len([s for s in RECORDS.values() if s.prefix])
    assert prefixed == {
        "G": "goal",
        "T": "task",
        "V": "version",
        "C": "claim",
        "X": "experiment",
        "D": "directive",
        "I": "intent",
        "N": "notice",
    }
    for spec in RECORDS.values():
        if not spec.prefix:
            assert spec.name in {"champion", "capability", "session", "coworld"}
    assert _common.PREFIXES == prefixed


def test_display_id():
    assert registry.display_id("G", 3) == "G-3"
    assert _common.display_id("T", 42) == "T-42"
    assert registry.display_id_for("experiment", 5) == "X-5"
    with pytest.raises(ValueError):
        registry.display_id("G", 0)
    with pytest.raises(ValueError):
        registry.display_id_for("champion", 1)


@pytest.mark.parametrize(
    ("text", "kind", "n"),
    [("G-3", "goal", 3), ("T-42", "task", 42), ("C-120", "claim", 120), ("I-9", "intent", 9)],
)
def test_parse_display_id_round_trips(text, kind, n):
    assert registry.parse_display_id(text) == (kind, n)
    assert registry.display_id(RECORDS[kind].prefix, n) == text


@pytest.mark.parametrize(
    "text", ["G-0", "g-3", "GX-1", "G3", "G-", "G-03", "G-3#methods", "Q-1", ""]
)
def test_parse_display_id_rejects_bad_forms(text):
    with pytest.raises(ValueError):
        registry.parse_display_id(text)


def test_record_from_row_formats_id_and_parses_json_fields():
    spec = RECORDS["goal"]
    row = {
        "id": 3,
        "title": "t",
        "end_state": "e",
        "reason": "r",
        "status": "active",
        "status_reason": None,
        "judgement": None,
        "tags": '["a", "b"]',
        "description": "",
        "where_it_stands": "",
        "updates_for_human": "",
        "created": "2026-10-28T00:00:00.000000Z",
        "created_by": "harness",
        "updated": "2026-10-28T00:00:00.000000Z",
    }
    record = registry.record_from_row(spec, row)
    assert record["id"] == "G-3"
    assert record["tags"] == ["a", "b"]
    assert record["title"] == "t"
    assert record["status_reason"] is None
    task_row = {"id": 1, "failure": None, "waiting_on": '{"kind": "xp"}', "checklist": "{}"}
    task = registry.record_from_row(RECORDS["task"], task_row)
    assert task == {"id": "T-1", "failure": None, "waiting_on": {"kind": "xp"}, "checklist": {}}
    champion = registry.record_from_row(
        RECORDS["champion"], {"league_id": "league_1", "state": "none"}
    )
    assert champion == {"league_id": "league_1", "state": "none"}


def test_record_from_row_accepts_sqlite_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate.apply_migrations(conn, "coworld")
    conn.execute(
        "INSERT INTO directives(text, scope, source, status, created, created_by)"
        " VALUES ('always test first', 'workspace', 'msg:1', 'active', 't', 'user:u1')"
    )
    row = conn.execute("SELECT * FROM directives").fetchone()
    record = registry.record_from_row(RECORDS["directive"], row)
    assert record["id"] == "D-1" and record["text"] == "always test first"
    assert record["retired"] is None


def test_spec_lookup_helpers():
    spec = RECORDS["task"]
    assert spec.field("status").enum == spec.statuses
    with pytest.raises(KeyError):
        spec.field("nope")
    assert {f.name for f in spec.json_fields} >= {"failure", "waiting_on", "checklist"}
    assert "stuck" in {f.name for f in spec.computed_fields}
    assert "stuck" not in {f.name for f in spec.stored_fields}
    assert {f.name for f in spec.required_fields} == {"type", "title", "description"}
    assert registry.spec_for_table("goals") is RECORDS["goal"]
    assert registry.spec_for_table("tasks") is RECORDS["task"]
    assert registry.spec_for_table("change_log") is None


def test_faithful_to_the_design_in_a_few_spots():
    task = RECORDS["task"]
    assert task.field("type").enum == ("research", "improvement", "ideation", "maintenance")
    assert task.field("priority").enum == ("normal", "high")
    assert task.field("outcome").enum == ("succeeded", "failed", "inconclusive", "abandoned")
    assert task.field("end_condition").enum == ("deliverable", "steps", "effort")
    assert not task.field("goal_id").required  # maintenance Tasks have no Goal
    assert "depends_on" in {f.name for f in task.computed_fields}
    claim = RECORDS["claim"]
    assert {"freshness", "contested", "evidence"} <= {f.name for f in claim.computed_fields}
    assert "40" in claim.field("summary").description
    assert "500" in claim.field("body").description
    version = RECORDS["version"]
    assert version.field("source").enum == ("built", "adopted")
    assert "based_on" in {f.name for f in version.computed_fields}
    assert "submission_status" in {f.name for f in version.computed_fields}
    experiment = RECORDS["experiment"]
    assert {f.name for f in experiment.required_fields} == {"summary", "hypothesis", "methods"}
    assert RECORDS["intent"].field("kind").enum == (
        "upload",
        "submit",
        "xp",
        "forum",
        "wiki",
        "run",
        "champion",
    )
    assert RECORDS["champion"].field("state").enum == (
        "pending",
        "processing",
        "placed",
        "rejected",
        "champion",
        "none",
    )
    assert RECORDS["notice"].store == "workspace"
    assert RECORDS["session"].store == "workspace"
    assert RECORDS["notice"].field("urgency").enum == ("low", "normal", "high")


# --- _common helpers ----------------------------------------------------------------------


def test_now_iso_format_and_clock_injection():
    from datetime import UTC, datetime

    fixed = datetime(2026, 10, 28, 21, 4, 5, 123456, tzinfo=UTC)
    assert _common.now_iso(lambda: fixed) == "2026-10-28T21:04:05.123456Z"
    naive = datetime(2026, 10, 28, 21, 4, 5)
    assert _common.now_iso(lambda: naive) == "2026-10-28T21:04:05.000000Z"
    real = _common.now_iso()
    assert len(real) == 27 and real.endswith("Z") and real[10] == "T"
    assert _common.parse_iso("2026-10-28T21:04:05.123456Z") == fixed
    assert _common.parse_iso(_common.now_iso(lambda: naive)).tzinfo is UTC


def test_json_helpers():
    assert _common.json_field({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert _common.json_field([]) == "[]"
    assert _common.parse_json('{"a": 1}') == {"a": 1}
    assert _common.parse_json(None) is None
    assert _common.parse_json("", default=[]) == []
    assert _common.parse_json(None, default={}) == {}


def test_require_and_word_count():
    problems: list[str] = []
    assert _common.require(problems, True, "never added") is True
    assert _common.require(problems, False, "first") is False
    assert _common.require(problems, 0, "second") is False
    assert problems == ["first", "second"]
    assert _common.word_count("one two  three\nfour") == 4
    assert _common.word_count("") == 0
    assert _common.word_count("  ") == 0


def test_fetch_row_raises_not_found(coworld_store):
    from research_core.store.errors import NotFound

    with pytest.raises(NotFound) as excinfo:
        _common.fetch_row(coworld_store, "goals", 7, display="G-7")
    assert excinfo.value.problems == ["G-7 does not exist"]

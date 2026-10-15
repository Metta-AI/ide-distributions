import datetime

from ide_research_core.io.docs import read_doc
from ide_research_core.kb.apply import apply
from ide_research_core.kb.lint import analyze
from ide_research_core.records.store import RecordStore

from .conftest import C_GRADE, C_VERIF

NOW = datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.UTC)


def claim_files(layout):
    return {path.name: path.read_text() for path in layout.claims.glob("*.md")}


def test_apply_writes_grade_and_demotes(kb_layout):
    store = RecordStore(kb_layout)
    bodies = {id_: store.load(id_)[1] for id_ in (C_GRADE, C_VERIF)}

    written = apply(kb_layout, analyze(kb_layout), NOW)

    assert store.load(C_GRADE)[0]["evidence_grade"] == "moderate"
    meta = store.load(C_VERIF)[0]
    assert meta["status"] == "belief"
    assert "VERIFIED_NOT_ELIGIBLE" in meta["status_reason"]
    assert NOW.isoformat() in meta["status_reason"]
    assert meta["status_history"][-1]["from"] == "verified"
    assert meta["status_history"][-1]["to"] == "belief"
    assert {id_: store.load(id_)[1] for id_ in bodies} == bodies  # claim text untouched
    assert store.validate_all()[1] == {}

    logs = sorted(p for p in written if p.parent == kb_layout.kb_log)
    assert len(logs) == 2
    entries = sorted((read_doc(p)[0] for p in logs), key=lambda m: m["finding"])
    assert entries == [
        {
            "at": NOW.isoformat(),
            "finding": "STATUS_GRADE_MISMATCH",
            "entity": C_GRADE,
            "before": "low",
            "after": "moderate",
        },
        {
            "at": NOW.isoformat(),
            "finding": "VERIFIED_NOT_ELIGIBLE",
            "entity": C_VERIF,
            "before": "verified",
            "after": "belief",
        },
    ]
    assert set(written) == set(logs) | {
        store.path_of(C_GRADE),
        store.path_of(C_VERIF),
    }


def test_apply_clears_its_findings(kb_layout):
    apply(kb_layout, analyze(kb_layout), NOW)
    codes = {f.code for f in analyze(kb_layout).findings}
    assert "STATUS_GRADE_MISMATCH" not in codes
    assert "VERIFIED_NOT_ELIGIBLE" not in codes


def test_second_apply_writes_nothing(kb_layout):
    apply(kb_layout, analyze(kb_layout), NOW)
    claims_before = claim_files(kb_layout)
    logs_before = sorted(kb_layout.kb_log.iterdir())

    assert apply(kb_layout, analyze(kb_layout), NOW) == []
    assert claim_files(kb_layout) == claims_before
    assert sorted(kb_layout.kb_log.iterdir()) == logs_before


def test_reapplying_a_stale_report_writes_nothing(kb_layout):
    report = analyze(kb_layout)
    apply(kb_layout, report, NOW)
    later = NOW + datetime.timedelta(minutes=5)
    assert apply(kb_layout, report, later) == []


def test_apply_ignores_findings_it_does_not_fix(kb_layout):
    report = analyze(kb_layout)
    report.findings[:] = [
        f
        for f in report.findings
        if f.code not in ("STATUS_GRADE_MISMATCH", "VERIFIED_NOT_ELIGIBLE")
    ]
    before = claim_files(kb_layout)
    assert apply(kb_layout, report, NOW) == []
    assert claim_files(kb_layout) == before


def test_finding_for_a_claim_archived_since_is_skipped(kb_layout):
    report = analyze(kb_layout)
    path = RecordStore(kb_layout).path_of(C_GRADE)
    path.rename(kb_layout.archive / path.name)
    written = apply(kb_layout, report, NOW)
    assert all(C_GRADE not in str(p) for p in written)
    assert [read_doc(p)[0]["entity"] for p in written if p.parent == kb_layout.kb_log] == [C_VERIF]

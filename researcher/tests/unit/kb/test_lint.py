from ide_research_core.io.docs import read_doc, write_doc
from ide_research_core.kb.lint import Finding, analyze
from ide_research_core.paths import Layout

from .conftest import (
    C_ARCH,
    C_CONTRA,
    C_GOOD,
    C_GRADE,
    C_MISS,
    C_ORPHAN,
    C_VERIF,
    H_MISS,
    HUB,
    KB_SMALL,
    TOPIC,
)


def codes_for(report, code):
    return [f for f in report.findings if f.code == code]


def entities(report, code):
    return [f.entity for f in codes_for(report, code)]


def test_fixture_has_exactly_one_finding_per_code():
    report = analyze(Layout(KB_SMALL))
    assert sorted((f.code, f.entity) for f in report.findings) == [
        ("ARCHIVED_STILL_CITED", TOPIC),
        ("HUB_CITES_MISSING_CLAIM", HUB),
        ("HUB_SENTENCE_UNCITED", HUB),
        ("LINK_UNRESOLVED", C_ORPHAN),
        ("ORPHAN_CLAIM", C_ORPHAN),
        ("STATUS_GRADE_MISMATCH", C_GRADE),
        ("UNRESOLVED_CONTRADICTION", C_CONTRA),
        ("VERIFIED_NOT_ELIGIBLE", C_VERIF),
    ]


def test_findings_are_sorted_and_deterministic():
    first, second = analyze(Layout(KB_SMALL)), analyze(Layout(KB_SMALL))
    assert first == second
    keys = [(f.code, f.entity, f.message) for f in first.findings]
    assert keys == sorted(keys)
    assert all(isinstance(f, Finding) for f in first.findings)


def test_link_unresolved_names_the_target():
    [finding] = codes_for(analyze(Layout(KB_SMALL)), "LINK_UNRESOLVED")
    assert H_MISS in finding.message


def test_link_to_archived_claim_resolves(kb_layout):
    path = next(kb_layout.claims.glob(f"{C_GRADE}--*.md"))
    meta, body = read_doc(path)
    write_doc(path, meta, body + f"Formerly [[{C_ARCH}]] and ar://claim/{C_ARCH}.\n")
    assert entities(analyze(kb_layout), "LINK_UNRESOLVED") == [C_ORPHAN]


def test_ar_link_with_wrong_type_is_unresolved(kb_layout):
    path = next(kb_layout.claims.glob(f"{C_GRADE}--*.md"))
    meta, body = read_doc(path)
    write_doc(path, meta, body + f"See ar://hypothesis/{C_GOOD}.\n")
    assert sorted(entities(analyze(kb_layout), "LINK_UNRESOLVED")) == sorted([C_GRADE, C_ORPHAN])


def test_orphan_claim_is_cited_by_a_hypothesis_body_link(kb_layout):
    path = next(kb_layout.hypotheses.glob("*.md"))
    meta, _ = read_doc(path)
    write_doc(path, meta, f"Builds on ar://claim/{C_ORPHAN}.\n")
    assert entities(analyze(kb_layout), "ORPHAN_CLAIM") == []


def test_self_link_does_not_cure_orphan(kb_layout):
    path = next(kb_layout.claims.glob(f"{C_ORPHAN}--*.md"))
    meta, body = read_doc(path)
    write_doc(path, meta, body + f"Self [[{C_ORPHAN}]].\n")
    assert entities(analyze(kb_layout), "ORPHAN_CLAIM") == [C_ORPHAN]


def test_hub_sentence_uncited_reports_the_line():
    [finding] = codes_for(analyze(Layout(KB_SMALL)), "HUB_SENTENCE_UNCITED")
    assert "Creeps respawn every 30 seconds." in finding.message


def test_hub_cites_missing_claim_once_for_body_and_front_matter():
    [finding] = codes_for(analyze(Layout(KB_SMALL)), "HUB_CITES_MISSING_CLAIM")
    assert C_MISS in finding.message


def test_contradiction_from_refuted_claim_is_not_unresolved():
    # C_GOOD carries a contradicting entry whose ref is the refuted claim.
    report = analyze(Layout(KB_SMALL))
    assert C_GOOD not in entities(report, "UNRESOLVED_CONTRADICTION")
    assert C_GOOD not in entities(report, "VERIFIED_NOT_ELIGIBLE")


def test_status_grade_mismatch_reports_stored_and_computed():
    [finding] = codes_for(analyze(Layout(KB_SMALL)), "STATUS_GRADE_MISMATCH")
    assert "low" in finding.message and "moderate" in finding.message


def test_missing_grade_is_a_mismatch(kb_layout):
    path = next(kb_layout.claims.glob(f"{C_GOOD}--*.md"))
    meta, body = read_doc(path)
    del meta["evidence_grade"]
    write_doc(path, meta, body)
    assert C_GOOD in entities(analyze(kb_layout), "STATUS_GRADE_MISMATCH")


def test_archived_still_cited_ignores_closed_topics(kb_layout):
    path = next(kb_layout.topics.glob("*.md"))
    meta, body = read_doc(path)
    write_doc(path, {**meta, "status": "closed"}, body)
    assert entities(analyze(kb_layout), "ARCHIVED_STILL_CITED") == []


def test_empty_workspace_has_no_findings(tmp_path):
    assert analyze(Layout(tmp_path)).findings == []

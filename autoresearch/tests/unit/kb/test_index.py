import json

from autoresearch.io.docs import write_doc
from autoresearch.kb.index import estimate_tokens, write_generated
from autoresearch.records.store import RecordStore

from .conftest import C_CONTRA, C_GOOD, C_ORPHAN, HUB, kb_id

VERSION = {
    "parents": [],
    "strategy_sha256": "a" * 64,
    "source_commit": "abc",
    "artifact_sha256": "b" * 64,
    "policy_name": "p",
}


def add_claim(layout, n, grade, topics=(), body="Extra claim."):
    id_ = kb_id("C", n)
    meta = {
        "id": id_,
        "kind": "mechanism",
        "status": "belief",
        "evidence": [],
        "evidence_grade": grade,
        "topics": list(topics),
        "relations": [],
    }
    write_doc(layout.claims / f"{id_}--extra.md", meta, body + "\n")
    return id_


def test_generated_files_written(kb_layout):
    paths = write_generated(kb_layout)
    assert [p.relative_to(kb_layout.root).as_posix() for p in paths] == [
        "kb/index.md",
        "kb/_generated/backlinks.json",
        "kb/_generated/contradictions.json",
        "kb/_generated/versions.jsonl",
    ]
    assert all(p.exists() for p in paths)


def test_byte_identical_on_rerun(kb_layout):
    first = [p.read_bytes() for p in write_generated(kb_layout)]
    second = [p.read_bytes() for p in write_generated(kb_layout)]
    assert first == second


def test_index_lists_hubs_then_claims_by_topic(kb_layout):
    add_claim(kb_layout, 20, "high", body="No topic here.")
    text = write_generated(kb_layout)[0].read_text()
    assert text.index("## Hubs") < text.index("## Combat") < text.index("## Unfiled")
    assert f"[Mechanics](ar://hub/{HUB})" in text
    assert (
        f"- [{C_GOOD}](ar://claim/{C_GOOD}) verified high — A basic attack deals 10 damage." in text
    )
    assert "belief high — No topic here." in text.split("## Unfiled")[1]
    assert "refuted" in text  # a non-live-status claim is still listed


def test_stale_claim_is_marked(kb_layout):
    RecordStore(kb_layout).set_status(C_GOOD, "stale", "game updated")
    text = write_generated(kb_layout)[0].read_text()
    assert f"[{C_GOOD}](ar://claim/{C_GOOD}) stale high" in text


def test_budget_drops_lowest_grade_first(kb_layout):
    full = write_generated(kb_layout)[0].read_text()
    ids_by_grade = {}
    for line in full.splitlines():
        if line.startswith("- [C-"):
            ids_by_grade.setdefault(line.split()[3], []).append(line.split("]")[0][3:])
    budget = estimate_tokens(full) - 1
    text = write_generated(kb_layout, token_budget=budget)[0].read_text()
    assert text.splitlines()[-1].endswith("claims omitted (budget)")
    # Exactly the dropped claims are the lowest-grade ones.
    dropped = [c for ids in ids_by_grade.values() for c in ids if f"[{c}]" not in text]
    assert len(dropped) >= 1
    assert all(c in ids_by_grade["very_low"] for c in dropped)
    assert f"… {len(dropped)} claims omitted (budget)" in text
    assert estimate_tokens(text) <= budget


def test_tight_budget_keeps_high_grade_last(kb_layout):
    text = write_generated(kb_layout, token_budget=1)[0].read_text()
    assert "## Hubs" in text
    assert "claims omitted (budget)" in text
    assert "ar://claim/" not in text


def test_backlinks_and_contradictions(kb_layout):
    paths = write_generated(kb_layout)
    backlinks = json.loads(paths[1].read_text())
    assert HUB in backlinks[C_GOOD]
    assert C_ORPHAN not in backlinks
    contradictions = json.loads(paths[2].read_text())
    assert list(contradictions) == [C_CONTRA]


def test_versions_view_folds_events(kb_layout):
    store = RecordStore(kb_layout)
    second = store.create("version", VERSION)
    first = store.create("version", VERSION)
    store.add_event(first, "uploaded", {"policy_version_id": "pv_1"})
    lines = write_generated(kb_layout)[3].read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["id"] for r in records] == sorted([first, second])
    by_id = {r["id"]: r for r in records}
    assert [e["kind"] for e in by_id[first]["events"]] == ["uploaded"]
    assert by_id[second]["events"] == []


GRADES = ("very_low", "low", "moderate", "high")


def survivors(layout, budget):
    text = write_generated(layout, token_budget=budget)[0].read_text()
    return {line.split("]")[0][3:] for line in text.splitlines() if line.startswith("- [C-")}


def test_truncation_order_across_grades_and_ids(tmp_path):
    from autoresearch.paths import Layout

    layout = Layout(tmp_path)
    # Ids 100..107, two per grade, assigned so id order differs from grade order.
    grade_of = {}
    for n, grade in zip(range(100, 108), ("high", "low", "very_low", "moderate") * 2, strict=True):
        grade_of[add_claim(layout, n, grade, body=f"Claim {n}.")] = grade
    total = len(grade_of)
    full = estimate_tokens(write_generated(layout)[0].read_text())

    drop_order = sorted(
        grade_of, key=lambda c: (GRADES.index(grade_of[c]), [-ord(ch) for ch in c])
    )  # lowest grade first, highest id first within a grade
    previous = set(grade_of)
    for budget in (full - 1, full * 3 // 4, full // 2, full // 4):
        alive = survivors(layout, budget)
        assert alive <= previous  # monotone: tighter budget never brings a claim back
        dropped = [c for c in drop_order if c not in alive]
        assert dropped == drop_order[: len(dropped)]  # always a prefix of the drop order
        assert len(dropped) == total - len(alive)
        previous = alive
    assert survivors(layout, full // 4) != set(grade_of)
    assert len(survivors(layout, full - 1)) < total

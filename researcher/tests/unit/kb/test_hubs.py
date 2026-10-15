from ide_research_core.io.docs import write_doc
from ide_research_core.kb.hubs import (
    HUB_DRIFT,
    HUB_OVERLAP,
    HUB_TOO_LARGE,
    hub_flags,
)

from .conftest import HUB, kb_id


def add_claim(layout, n, cites=()):
    id_ = kb_id("C", n)
    body = " ".join(f"[[{c}]]" for c in cites) or "Standalone."
    meta = {
        "id": id_,
        "kind": "mechanism",
        "status": "belief",
        "evidence": [],
        "evidence_grade": "low",
        "topics": [],
        "relations": [],
    }
    write_doc(layout.claims / f"{id_}--c.md", meta, body + "\n")
    return id_


def add_hub(layout, n, claims):
    id_ = kb_id("HUB", n)
    body = "\n".join(f"Sentence [[{c}]]." for c in claims)
    write_doc(
        layout.hubs / f"{id_}--h.md", {"id": id_, "title": f"Hub {n}", "claims": claims}, body
    )
    return id_


def codes(findings):
    return [(f.code, f.entity) for f in findings]


def test_fixture_has_no_hub_flags(kb_layout):
    assert hub_flags(kb_layout) == []


def test_too_large(kb_layout):
    assert codes(hub_flags(kb_layout, size_limit_tokens=10)) == [(HUB_TOO_LARGE, HUB)]


def test_drift_needs_two_components_of_three(kb_layout):
    ids = [add_claim(kb_layout, 30)]
    ids += [add_claim(kb_layout, n, cites=[ids[0]]) for n in (31, 32)]  # component A (3)
    other = add_claim(kb_layout, 33)
    ids += [other] + [add_claim(kb_layout, n, cites=[other]) for n in (34, 35)]  # component B (3)
    hub = add_hub(kb_layout, 5, ids)
    assert (HUB_DRIFT, hub) in codes(hub_flags(kb_layout))


def test_no_drift_with_a_small_second_component(kb_layout):
    first = add_claim(kb_layout, 30)
    ids = [first] + [add_claim(kb_layout, n, cites=[first]) for n in (31, 32)]
    ids += [add_claim(kb_layout, 33), add_claim(kb_layout, 34)]  # two singletons
    add_hub(kb_layout, 5, ids)
    assert HUB_DRIFT not in [c for c, _ in codes(hub_flags(kb_layout))]


def test_overlap(kb_layout):
    ids = [add_claim(kb_layout, n) for n in (40, 41, 42, 43)]
    a = add_hub(kb_layout, 6, ids)
    b = add_hub(kb_layout, 7, ids[:3])  # Jaccard 3/4
    assert (HUB_OVERLAP, f"{a},{b}") in codes(hub_flags(kb_layout))


def test_low_overlap_not_flagged(kb_layout):
    ids = [add_claim(kb_layout, n) for n in (40, 41, 42, 43)]
    add_hub(kb_layout, 6, ids[:2])
    add_hub(kb_layout, 7, ids[1:3])  # Jaccard 1/3
    assert HUB_OVERLAP not in [c for c, _ in codes(hub_flags(kb_layout))]

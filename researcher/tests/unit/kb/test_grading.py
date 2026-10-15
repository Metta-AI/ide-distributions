import pytest

from autoresearch.kb.grading import (
    Ev,
    effective_tier,
    grade,
    unresolved_contradictions,
    verification_eligible,
)


def ev(ref, tier, group=None, direct=True, stance="supports", target=None):
    return Ev(
        ref=ref, tier=tier, direct=direct, stance=stance, source_group=group or ref, target=target
    )


def test_direct_S_verifies_mechanism():
    assert verification_eligible("mechanism", [ev("e1", "S")])


def test_S_alone_never_verifies_behaviour():
    evs = [ev("e1", "S"), ev("e2", "A")]
    for kind in ("behaviour", "strategy", "opponent"):
        assert not verification_eligible(kind, evs)


def test_two_independent_C_verify_behaviour():
    assert verification_eligible("behaviour", [ev("e1", "C", "g1"), ev("e2", "C", "g2")])


def test_two_C_same_source_group_do_not():
    assert not verification_eligible("behaviour", [ev("e1", "C", "g"), ev("e2", "C", "g")])


def test_single_D_never_verifies():
    for kind in ("mechanism", "behaviour", "strategy", "opponent", "platform"):
        assert not verification_eligible(kind, [ev("e1", "D")])


def test_unresolved_contradiction_blocks():
    evs = [ev("e1", "S"), ev("e2", "C", stance="contradicts")]
    assert not verification_eligible("mechanism", evs)


def test_resolved_contradiction_allows():
    evs = [
        ev("e1", "S"),
        ev("e2", "C", stance="contradicts"),
        ev("e3", "C", stance="resolves", target="e2"),
    ]
    assert verification_eligible("mechanism", evs)
    assert unresolved_contradictions(evs) == []


def test_superseded_contradiction_allows():
    evs = [ev("e1", "S"), ev("e2", "C", stance="contradicts")]
    assert verification_eligible("mechanism", evs, superseded_or_refuted_refs={"e2"})


def test_indirect_S_counts_as_A():
    assert effective_tier(ev("e1", "S", direct=False)) == "A"
    assert not verification_eligible("mechanism", [ev("e1", "S", direct=False)])
    assert verification_eligible("platform", [ev("e1", "S", direct=False)])
    assert verification_eligible("mechanism", [ev("e1", "S", direct=False), ev("e2", "B")])


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"), ("E", "E")],
)
def test_indirect_drops_one_tier(tier, expected):
    assert effective_tier(ev("e1", tier, direct=False)) == expected


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        verification_eligible("rumour", [])


def test_platform_needs_A_or_S():
    assert verification_eligible("platform", [ev("e1", "A")])
    assert not verification_eligible("platform", [ev("e1", "B")])


@pytest.mark.parametrize(
    ("evs", "expected"),
    [
        ([ev("e1", "S")], "high"),
        ([ev("e1", "A", "g1"), ev("e2", "B", "g2")], "high"),
        ([ev("e1", "A", "g"), ev("e2", "B", "g")], "moderate"),
        ([ev("e1", "B")], "moderate"),
        ([ev("e1", "C", "g1"), ev("e2", "C", "g2")], "moderate"),
        ([ev("e1", "C")], "low"),
        ([ev("e1", "D")], "low"),
        ([ev("e1", "E")], "very_low"),
        ([], "very_low"),
        ([ev("e1", "S"), ev("e2", "A", stance="contradicts")], "high"),
        ([ev("e1", "S"), ev("e2", "S", stance="contradicts")], "moderate"),
        ([ev("e1", "B"), ev("e2", "B", stance="contradicts")], "low"),
        ([ev("e1", "B"), ev("e2", "C", stance="contradicts")], "moderate"),
        (
            [
                ev("e1", "B"),
                ev("e2", "B", stance="contradicts"),
                ev("e3", "A", stance="resolves", target="e2"),
            ],
            "moderate",
        ),
    ],
)
def test_grade_table(evs, expected):
    assert grade(evs) == expected

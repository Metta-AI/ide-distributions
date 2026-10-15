"""Evidence grading and verification eligibility for knowledge-base claims.

Pure functions, no IO. The linter calls these; the LLM never computes grades.
Tiers: S game source, A official docs, B controlled experiment, C several
hash-verified replays, D single replay, E forum post or other agent.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

Tier = Literal["S", "A", "B", "C", "D", "E"]
Grade = Literal["high", "moderate", "low", "very_low"]

TIER_ORDER: tuple[Tier, ...] = ("S", "A", "B", "C", "D", "E")
_GRADE_ORDER: tuple[Grade, ...] = ("high", "moderate", "low", "very_low")
CLAIM_KINDS = ("mechanism", "behaviour", "strategy", "opponent", "platform")
_PLAY_KINDS = ("behaviour", "strategy", "opponent")


@dataclass(frozen=True)
class Ev:
    ref: str
    tier: Tier
    direct: bool
    stance: Literal["supports", "contradicts", "resolves"]
    source_group: str  # same source_group means not independent
    target: str | None = None  # for "resolves": the ref of the contradiction it resolves


def effective_tier(e: Ev) -> Tier:
    """Indirect S counts as A; other indirect tiers drop one; E stays E."""
    if e.direct:
        return e.tier
    index = TIER_ORDER.index(e.tier)
    if e.tier == "S":
        return "A"
    return TIER_ORDER[min(index + 1, len(TIER_ORDER) - 1)]


def unresolved_contradictions(
    evs: list[Ev], superseded_or_refuted_refs: Collection[str] = frozenset()
) -> list[Ev]:
    """Contradicting entries not targeted by a "resolves" entry and not from a superseded/refuted claim."""
    resolved = {e.target for e in evs if e.stance == "resolves"}
    return [
        e
        for e in evs
        if e.stance == "contradicts"
        and e.ref not in resolved
        and e.ref not in superseded_or_refuted_refs
    ]


def _supporting(evs: list[Ev]) -> list[Ev]:
    return [e for e in evs if e.stance == "supports"]


def _independent_count(evs: list[Ev]) -> int:
    return len({e.source_group for e in evs})


def grade(evs: list[Ev], superseded_or_refuted_refs: Collection[str] = frozenset()) -> Grade:
    supporting = _supporting(evs)
    tiers = [effective_tier(e) for e in supporting]
    ab = [e for e in supporting if effective_tier(e) in ("A", "B")]
    c = [e for e in supporting if effective_tier(e) == "C"]

    if "S" in tiers or _independent_count(ab) >= 2:
        level = 0
    elif ab or _independent_count(c) >= 2:
        level = 1
    elif c or "D" in tiers:
        level = 2
    else:
        level = 3

    if tiers and level < 3:
        best = min(TIER_ORDER.index(t) for t in tiers)
        contradictions = unresolved_contradictions(evs, superseded_or_refuted_refs)
        if any(TIER_ORDER.index(effective_tier(e)) <= best for e in contradictions):
            level += 1
    return _GRADE_ORDER[level]


def verification_eligible(
    kind: str, evs: list[Ev], superseded_or_refuted_refs: Collection[str] = frozenset()
) -> bool:
    if kind not in CLAIM_KINDS:
        raise ValueError(f"unknown claim kind: {kind!r}")
    if unresolved_contradictions(evs, superseded_or_refuted_refs):
        return False
    supporting = _supporting(evs)

    if kind == "mechanism":
        if any(e.direct and e.tier == "S" for e in supporting):
            return True
        return _independent_count([e for e in supporting if effective_tier(e) in ("A", "B")]) >= 2
    if kind == "platform":
        return any(effective_tier(e) in ("S", "A") for e in supporting)

    # behaviour / strategy / opponent: needs evidence from actual play.
    play = [e for e in supporting if e.tier in ("B", "C") and effective_tier(e) in ("B", "C")]
    if any(effective_tier(e) == "B" for e in play):
        return True
    return _independent_count([e for e in play if effective_tier(e) == "C"]) >= 2

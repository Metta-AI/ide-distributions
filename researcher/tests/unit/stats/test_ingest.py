import pytest

from ide_research_core.stats.design import EvaluationDesign, MetricSpec, signed
from ide_research_core.stats.ingest import EpisodeRow, ingest, ingest_rows, pair

UP = MetricSpec("score", "up", "primary", 0.5, None)
DOWN = MetricSpec("errors", "down", "agent_guardrail", None, 0.1)
DESIGN = EvaluationDesign(
    game_version="65",
    baseline_version="pv-base",
    seats=(0, 1),
    opponents=("pv-opp",),
    seeds=(1, 2, 3),
    metrics=(UP, DOWN),
    k=0.5,
    delta={},
    n_min=10,
    noise_rev="abc",
)


def row(
    episode_id,
    policy="pv-base",
    seed=1,
    version="65",
    seats=(0, 1),
    opponents=("pv-opp",),
    metrics=None,
    status="completed",
):
    return EpisodeRow(
        episode_id,
        version,
        policy,
        seed,
        seats,
        opponents,
        {"score": 1.0, "errors": 2.0} if metrics is None else metrics,
        status,
    )


def test_ingest_rows_drops_and_counts():
    rows = [
        row("a"),
        row("a"),  # duplicate id
        row("b", status="failed"),
        row("c", version="64"),
        row("d", metrics={"score": 1.0}),
        row("e", metrics={}),
    ]
    kept, report = ingest_rows(rows, "65", ["score", "errors"])
    assert [r.episode_id for r in kept] == ["a"]
    assert (report.kept, report.duplicates, report.failed, report.wrong_version) == (1, 1, 1, 1)
    assert report.missing_metrics == ["errors", "score"]


def test_ingest_drops_wrong_seats_and_opponents():
    rows = [row("a"), row("b", seats=(1, 0)), row("c", opponents=("other",))]
    kept, report = ingest(rows, DESIGN)
    assert [r.episode_id for r in kept] == ["a"]
    assert report.wrong_seats == 2


def test_ingest_requests_design_metrics():
    kept, report = ingest([row("a", metrics={"score": 1.0})], DESIGN)
    assert kept == [] and report.missing_metrics == ["errors"]


def test_pair_matches_by_key_and_never_mixes_versions():
    rows = [
        row("b1", "pv-base", seed=1),
        row("c1", "pv-cand", seed=1),
        row("b2", "pv-base", seed=2),
        row("c2", "pv-cand", seed=2, version="64"),
        row("x", "pv-other", seed=3),
    ]
    pairs = pair(rows, DESIGN, "pv-cand")
    assert [(b.episode_id, c.episode_id) for b, c in pairs] == [("b1", "c1")]


def test_pair_unique_first_by_episode_id():
    rows = [
        row("b2", "pv-base"),
        row("b1", "pv-base"),
        row("c2", "pv-cand"),
        row("c1", "pv-cand"),
    ]
    pairs = pair(rows, DESIGN, "pv-cand")
    assert [(b.episode_id, c.episode_id) for b, c in pairs] == [("b1", "c1")]


def test_pair_requires_same_seats_and_opponents():
    rows = [row("b", "pv-base"), row("c", "pv-cand", seats=(1, 0))]
    assert pair(rows, DESIGN, "pv-cand") == []


def test_signed_negates_down():
    assert signed(2.0, UP) == 2.0
    assert signed(2.0, DOWN) == -2.0


def test_ingest_drops_seed_outside_design():
    kept, report = ingest([row("a", seed=1), row("b", seed=99)], DESIGN)
    assert [r.episode_id for r in kept] == ["a"]
    assert report.wrong_seed == 1


def test_ingest_and_pair_accept_json_lists():
    rows = [row("b", "pv-base", seats=[0, 1], opponents=["pv-opp"]), row("c", "pv-cand")]
    kept, report = ingest(rows, DESIGN)
    assert report.kept == 2
    assert len(pair(kept, DESIGN, "pv-cand")) == 1


def test_pair_rejects_candidate_equal_to_baseline():
    with pytest.raises(ValueError):
        pair([row("a")], DESIGN, "pv-base")

from pathlib import Path

from records.fixtures import EXP_DESIGNED

from autoresearch.games.loader import load_game
from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore
from autoresearch.stats.design import EvaluationDesign, MetricSpec
from autoresearch.stats.ingest import EpisodeRow, ingest, pair

AUTORESEARCH_DIR = Path(__file__).resolve().parents[3]


def test_preregister_bind_ingest_pair_leaves_design_frozen(tmp_path):
    game = load_game(AUTORESEARCH_DIR, "fakegame")
    directions = {m.name: m.direction for m in game.outcome_metrics}
    metrics = tuple(
        MetricSpec(name, directions[name], "performance", None, None)
        for name in game.performance_guardrails
    ) + (MetricSpec("errors", directions["errors"], "primary", 0.1, None),)
    design = EvaluationDesign(
        "65",
        "pv-base",
        (0, 1),
        ("pv-opp",),
        (1, 2, 3),
        metrics,
        0.5,
        {"score": 0.2},
        game.min_episodes_floor,
        "rev1",
    )
    frozen = design.to_dict()
    store = RecordStore(Layout(tmp_path))
    exp = store.create("experiment", {**EXP_DESIGNED, "evaluation_design": frozen})
    store.set_status(exp, "built")
    store.set_status(exp, "uploaded")
    store.update(
        exp, {"candidate_binding": {"version_id": "V-1", "platform_policy_version_id": "pv-cand"}}
    )

    binding = store.load(exp)[0]["candidate_binding"]
    rows = [
        EpisodeRow(
            f"{side}{seed}",
            "65",
            pid,
            seed,
            (0, 1),
            ("pv-opp",),
            {"score": float(seed), "errors": 1.0},
            "completed",
        )
        for side, pid in (("b", "pv-base"), ("c", binding["platform_policy_version_id"]))
        for seed in (1, 2, 3)
    ]
    kept, report = ingest(rows, design)
    pairs = pair(kept, design, binding["platform_policy_version_id"])

    assert report.kept == 6 and len(pairs) == 3
    assert store.load(exp)[0]["evaluation_design"] == frozen

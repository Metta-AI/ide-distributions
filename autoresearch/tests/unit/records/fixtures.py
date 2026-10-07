"""Fully populated record metadata for each entity (every required schema field set; no `id`,
which the store assigns)."""

TOPIC = {
    "title": "Increase last hits",
    "objective": "Heroes secure more last hits on lane creeps without dying more.",
    "metrics": [{"name": "last_hits_per_1k_ticks", "direction": "up"}],
    "guardrails": [{"name": "deaths_per_1k_ticks", "direction": "down", "tolerance": 0.05}],
    "origin": {"kind": "agent"},
    "priority": 2,
    "status": "open",
    "hypotheses": [],
    "hubs": [],
    "claims": [],
    "strategy_components": ["SK.last_hit"],
}

HYP = {
    "statement": "Last hits are missed because heroes attack before the creep is in kill range.",
    "prediction": "Waiting until creep HP <= hero damage raises last hits per 1k ticks.",
    "topics": ["T-1"],
    "parents": [],
    "source": ["J-1"],
    "lever": "SK.last_hit",
    "state": "proposed",
    "experiments": [],
}

EXP_DESIGNED = {
    "hypothesis": "H-1",
    "parents": [],
    "mode": "experimentation",
    "lever": "SK.last_hit",
    "baseline": "V-0",
    "change": {"components": ["SK.last_hit"], "description": "Attack only in kill range."},
    "evaluation_design": {
        "primary_metrics": [
            {"name": "last_hits_per_1k_ticks", "direction": "up", "min_effect": 0.5}
        ],
        "guardrails": [{"name": "deaths_per_1k_ticks", "direction": "down", "tolerance": 0.05}],
        "performance_guardrails": [{"name": "glory", "delta": 0.4}],
        "n_min": 40,
        "noise_rev": "abc123",
    },
    "environment": {
        "game_id": "gota",
        "coworld_version": "2026.10.6.1",
        "game_version": "65",
        "manifest_sha256": "0" * 64,
    },
    "xp_requests": [],
    "results": [],
    "status": "designed",
    "lifecycle": {"launch_attempts": 0},
}

RESULT = {
    "experiment": "E-1",
    "xp_requests": ["xp_1"],
    "evidence": ["EV-1"],
    "policy_versions": {"baseline": "V-0", "candidate": "V-1"},
    "game_version": "65",
    "episodes_completed": 40,
    "metrics": {"last_hits_per_1k_ticks": {"diff": 0.7, "ci": [0.2, 1.1]}},
    "validity": {"status": "valid"},
}

CLAIM = {
    "kind": "mechanism",
    "status": "belief",
    "confidence": 0.7,
    "evidence": [],
    "revise_if": "A replay shows damage other than 10.",
    "relations": [],
    "topics": ["T-1"],
    "valid_from_version": "65",
    "last_checked_version": "65",
}

VERSION = {
    "parents": ["V-0"],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-gota",
    "gates": {"g1": "pass"},
}

JOURNAL = {
    "iteration": 1,
    "mode": "planning",
    "card": "t_1",
    "doctrine_version": "1",
    "refs": [],
}

JOURNAL_BODY = "Planned the week.\n\n## Decisions\n\n- d1: Prioritise T-1. Reason: most leverage.\n"

"""Freeze an evaluation design at preregistration from the experiment request and noise record."""

from autoresearch.games.loader import Game
from autoresearch.stats.design import EvaluationDesign, MetricSpec
from autoresearch.stats.noise import NoiseError, sd_for
from autoresearch.stats.power import delta, min_episodes

DEFAULT_K = 0.5


def freeze_design(
    experiment_meta: dict,
    game: Game,
    noise: dict,
    planned_pairs: int,
    noise_rev: str | None = None,
) -> EvaluationDesign:
    """Build the design: delta per performance metric and N from the noise record.

    ``experiment_meta["evaluation_design_request"]`` holds::

        {baseline_version: str, seats: [int], opponents: [str], seeds: [int],
         primary_metrics: [{name, direction, min_effect}],
         guardrails: [{name, direction, tolerance}],
         k: float (optional, default 0.5)}

    Performance metrics come from ``game.performance_guardrails`` (directions from
    ``game.outcome_metrics``). N is the largest ``min_episodes`` over primary metrics, at least
    the game's floor. The noise revision is ``noise["_rev"]`` if present, else ``noise_rev``.
    """
    request = experiment_meta["evaluation_design_request"]
    revision = noise.get("_rev") or noise_rev
    if not revision:
        raise NoiseError("no noise revision: pass noise_rev or include _rev in the record")
    k = request.get("k", DEFAULT_K)
    directions = {m.name: m.direction for m in game.outcome_metrics}
    primary = [
        MetricSpec(m["name"], m["direction"], "primary", m["min_effect"], None)
        for m in request["primary_metrics"]
    ]
    guardrails = [
        MetricSpec(m["name"], m["direction"], "agent_guardrail", None, m["tolerance"])
        for m in request["guardrails"]
    ]
    performance = [
        MetricSpec(name, directions[name], "performance", None, None)
        for name in game.performance_guardrails
    ]
    design = EvaluationDesign(
        game_version=noise["game_version"],
        baseline_version=request["baseline_version"],
        seats=tuple(request["seats"]),
        opponents=tuple(request["opponents"]),
        seeds=tuple(request["seeds"]),
        metrics=tuple(primary + guardrails + performance),
        k=k,
        delta={m.name: delta(sd_for(noise, m.name), planned_pairs, k) for m in performance},
        n_min=max(
            min_episodes(sd_for(noise, m.name), m.min_effect, floor=game.min_episodes_floor)
            for m in primary
        ),
        noise_rev=revision,
    )
    design.validate()
    return design

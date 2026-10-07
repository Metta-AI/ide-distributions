"""Freeze an evaluation design at preregistration from the experiment request and noise record."""

from autoresearch.games.loader import Game
from autoresearch.stats.design import DesignError, EvaluationDesign, MetricSpec
from autoresearch.stats.noise import sd_for
from autoresearch.stats.power import delta, min_episodes

DEFAULT_K = 0.5


def freeze_design(
    experiment_meta: dict,
    game: Game,
    noise: dict,
    planned_pairs: int,
    noise_rev: str,
) -> EvaluationDesign:
    """Build the design: delta per performance metric and N from the noise record.

    ``experiment_meta["evaluation_design_request"]`` holds::

        {baseline_version: str, seats: [int], opponents: [str], seeds: [int],
         primary_metrics: [{name, direction, min_effect}],
         guardrails: [{name, direction, tolerance}],
         k: float (optional, default 0.5)}

    Performance metrics come from ``game.performance_guardrails`` (directions from
    ``game.outcome_metrics``). N is the largest ``min_episodes`` over primary metrics, at least
    the game's floor. ``noise_rev`` is the revision returned by ``write_noise``.

    Raises DesignError for a missing or malformed request, an empty primary list, or a
    performance guardrail that is not a game outcome metric.
    """
    try:
        return _freeze(experiment_meta, game, noise, planned_pairs, noise_rev)
    except KeyError as error:
        raise DesignError(f"evaluation_design_request is missing {error.args[0]!r}") from error


def _freeze(experiment_meta, game, noise, planned_pairs, noise_rev) -> EvaluationDesign:
    request = experiment_meta["evaluation_design_request"]
    if not request.get("primary_metrics"):
        raise DesignError("evaluation_design_request.primary_metrics is empty")
    if not noise_rev:
        raise DesignError("noise_rev is empty")
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
    unknown = [name for name in game.performance_guardrails if name not in directions]
    if unknown:
        raise DesignError(f"performance guardrails not in game outcome metrics: {unknown}")
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
        noise_rev=noise_rev,
    )
    design.validate()
    return design

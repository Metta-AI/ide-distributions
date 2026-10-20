"""Freeze an evaluation design at preregistration from the experiment request and noise record."""

from ide_research_core.games.loader import Game
from ide_research_core.stats.design import DesignError, EvaluationDesign, MetricSpec
from ide_research_core.stats.noise import sd_for
from ide_research_core.stats.power import delta, min_episodes

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

        {baseline_version: str, seats: [int], seeds: [int],
         opponent_count: int  (sampled line-ups, A12; the normal case), or
         opponents: [str] + opponent_rationale: str  (a narrow fixed set, with the reason),
         primary_metrics: [{name, direction, min_effect}],
         guardrails: [{name, direction, tolerance}],
         k: float (optional, default 0.5)}

    With ``opponent_count`` the caller (``stats design``) has already sampled the league and
    added ``lineups`` and ``league_snapshot`` to the request (``stats.opponents``); they are
    frozen as given. A game with no opponents passes neither (``opponents: []``).

    Performance metrics come from ``game.performance_guardrails`` (directions from
    ``game.outcome_metrics``). N is the largest ``min_episodes`` over primary metrics, at least
    the game's floor. ``noise_rev`` is the revision returned by ``write_noise``.

    Raises DesignError for a missing or malformed request, an empty primary list, a
    performance guardrail that is not a game outcome metric, ``opponent_count`` together with
    ``opponents`` or without the sampled line-ups, or a fixed ``opponents`` set without
    ``opponent_rationale``.
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
    opponents, lineups, snapshot, rationale = _opponents(request)
    design = EvaluationDesign(
        game_version=noise["game_version"],
        baseline_version=request["baseline_version"],
        seats=tuple(request["seats"]),
        opponents=opponents,
        seeds=tuple(request["seeds"]),
        metrics=tuple(primary + guardrails + performance),
        k=k,
        delta={m.name: delta(sd_for(noise, m.name), planned_pairs, k) for m in performance},
        n_min=max(
            min_episodes(sd_for(noise, m.name), m.min_effect, floor=game.min_episodes_floor)
            for m in primary
        ),
        noise_rev=noise_rev,
        lineups=lineups,
        league_snapshot=snapshot,
        opponent_rationale=rationale,
    )
    design.validate()
    return design


def _opponents(request: dict) -> tuple[tuple[str, ...], dict | None, dict | None, str | None]:
    """(opponents, lineups, league_snapshot, opponent_rationale) from the request (A12)."""
    fixed = tuple(request.get("opponents") or ())
    rationale = (request.get("opponent_rationale") or "").strip() or None
    if "opponent_count" in request:
        if fixed:
            raise DesignError("give opponent_count (sampled) or opponents (fixed), not both")
        if "lineups" not in request or "league_snapshot" not in request:
            raise DesignError("opponent_count needs the sampled lineups and league_snapshot")
        lineups = {str(seed): tuple(lineup) for seed, lineup in request["lineups"].items()}
        return (), lineups, request["league_snapshot"], None
    if fixed and rationale is None:
        raise DesignError(
            "a fixed opponents set needs opponent_rationale (why these opponents, for example "
            '"does this beat X specifically"); otherwise give opponent_count to sample the '
            "league (A12)"
        )
    return fixed, None, None, rationale

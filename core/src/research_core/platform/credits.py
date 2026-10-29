"""Credits (design C.2, amendment A4.4): read the balance, estimate a request, say it in a line.

The platform's credits are the only gate on XP requests: a request the platform refuses for
lack of credits comes back as a 402 (``CreditsRefused``) and the tool reports it. Nothing here
pre-checks the balance; :func:`estimate` exists so the result and the briefing can say what a
request is likely to cost, and :func:`describe` renders one line about the balance.

There is no cost endpoint. ``Credits.recent_requests`` (the platform's
``ExperienceRequestComputeCostRow`` rows: ``episodes`` and ``compute_cost_credits``) gives the
cost of recent requests after the fact; without that history the mixin front matter's
``cost_per_episode`` stands in. Stdlib only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from research_core.platform.protocol import Credits

if TYPE_CHECKING:  # pragma: no cover
    from research_core.platform.protocol import Platform


def read(platform: Platform) -> Credits:
    """The Player's credits as the platform reports them."""
    return platform.credits()


def estimate(
    episodes: int, *, credits: Credits | None, cost_per_episode: float | None
) -> float | None:
    """The likely cost of ``episodes`` episodes, or ``None`` when nothing says.

    The recent requests' cost per episode (total credits over total episodes, so a large
    request weighs more than a small one) is used when there is history; otherwise the
    mixin's ``cost_per_episode``; otherwise there is no estimate.
    """
    per_episode = recent_cost_per_episode(credits) if credits is not None else None
    if per_episode is None:
        per_episode = cost_per_episode
    if per_episode is None:
        return None
    return per_episode * episodes


def recent_cost_per_episode(credits: Credits) -> float | None:
    """Credits per episode over the recent requests that report both numbers."""
    total_cost = 0.0
    total_episodes = 0
    for row in credits.recent_requests:
        cost, count = row.get("compute_cost_credits"), row.get("episodes")
        if not _is_number(cost) or not _is_number(count) or count <= 0:
            continue
        total_cost += float(cost)
        total_episodes += int(count)
    if total_episodes == 0:
        return None
    return total_cost / total_episodes


def describe(credits: Credits | None) -> str:
    """One line for a tool result or the briefing, such as
    ``credits: 99 available of 100; 1 spent this period; next refill 100 at 2026-10-29T…``."""
    if credits is None:
        return "credits: unknown (the platform did not answer)"
    parts = [f"credits: {_number(credits.balance)} available"]
    if credits.max is not None:
        parts[0] += f" of {_number(credits.max)}"
    if credits.spent_this_period is not None:
        parts.append(f"{_number(credits.spent_this_period)} spent this period")
    if credits.next_refill_at is not None:
        refill = f"next refill at {credits.next_refill_at}"
        if credits.refill_amount is not None:
            refill = f"next refill {_number(credits.refill_amount)} at {credits.next_refill_at}"
        parts.append(refill)
    return "; ".join(parts)


def _number(value: float) -> str:
    return f"{value:g}"


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)

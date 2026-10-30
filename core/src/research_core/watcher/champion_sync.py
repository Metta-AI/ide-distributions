"""Step 3: keep the champion records in step with the platform (design B.6, B.13; A4.9).

Every ten minutes (the Coworld store's meta ``champion_synced_at``) the step reads the
Player's memberships for every league of the active Coworld: the leagues whose ``coworld_id``
is the active id, or every league the platform lists when none names it (A4.9: not only the
leagues the workspace submitted to). For each league:

* a membership flagged ``is_champion`` is the champion: state ``champion``;
* otherwise the Player's newest membership stands for it with its platform status mapped onto
  the champion states (``competing`` is ``placed``; ``pending``, ``processing`` and
  ``rejected`` keep their names);
* no membership at all is ``none``; a league the store never recorded is left alone then,
  because writing ``none`` for an empty league would raise ``champion.changed`` on the first
  tick of every new workspace.

``champion.sync`` writes the row and emits ``champion.changed`` only when the policy version or
the state differs (a change made outside the workspace, or one the submit tool already
recorded); the management wake on that event is the wake step's. A platform error leaves the
stamp alone so the next tick tries again.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from research_core.platform.errors import PlatformError
from research_core.store import champion
from research_core.store._common import now_iso, parse_iso

if TYPE_CHECKING:  # pragma: no cover
    from research_core.platform.protocol import League, Membership
    from research_core.watcher.context import TickContext

SYNC_INTERVAL = timedelta(minutes=10)
SYNCED_AT = "champion_synced_at"
# A membership's platform status as a champion state when the membership is not the champion.
MEMBERSHIP_STATES = {
    "competing": "placed",
    "placed": "placed",
    "pending": "pending",
    "processing": "processing",
    "rejected": "rejected",
}


def run(ctx: TickContext, *, force: bool = False) -> dict[str, Any]:
    if ctx.store is None:
        return {"skipped": "no active Coworld"}
    last = ctx.store.get_meta(SYNCED_AT)
    if not force and last and ctx.now - parse_iso(last) < SYNC_INTERVAL:
        return {"skipped": "synced recently", "synced_at": last}
    try:
        report = sync(ctx)
    except PlatformError as error:
        return {"error": f"{type(error).__name__}: {error.detail}", "route": error.route}
    ctx.store.set_meta(SYNCED_AT, now_iso(lambda: ctx.now))
    return report


def sync(ctx: TickContext) -> dict[str, Any]:
    """Compare every league of the active Coworld with the platform; returns what changed."""
    platform = ctx.platform
    leagues = leagues_of(ctx)
    champions = {m.league_id: m for m in platform.memberships(mine=True, champions_only=True)}
    mine = platform.memberships(mine=True)
    report: dict[str, Any] = {"leagues": [league.id for league in leagues], "changed": []}
    for league in leagues:
        membership = champions.get(league.id)
        if membership is not None:
            state = "champion"
        else:
            membership = _representative(mine, league.id)
            state = MEMBERSHIP_STATES.get(membership.status, "none") if membership else "none"
        before = champion.read(ctx.store, league.id)
        if state == "none" and before is None:
            continue
        record = champion.sync(
            ctx.store,
            ctx.actor,
            league_id=league.id,
            division_id=membership.division_id if membership else None,
            membership_id=membership.id if membership else None,
            policy_version_id=membership.policy_version.id if state != "none" else None,
            state=state,
        )
        if (
            before is None
            or before["state"] != record["state"]
            or before["policy_version_id"] != record["policy_version_id"]
        ):
            report["changed"].append(
                {
                    "league_id": league.id,
                    "state": record["state"],
                    "policy_version_id": record["policy_version_id"],
                }
            )
    return report


def leagues_of(ctx: TickContext) -> list[League]:
    """The active Coworld's leagues: those naming its id, else every league the platform lists."""
    leagues = ctx.platform.leagues()
    coworld_id = ctx.coworld.coworld_id if ctx.coworld is not None else None
    own = [league for league in leagues if league.coworld_id == coworld_id]
    return own or list(leagues)


def _representative(memberships: list[Membership], league_id: str) -> Membership | None:
    """The Player's newest membership in the league, when it has one."""
    in_league = [m for m in memberships if m.league_id == league_id]
    return in_league[-1] if in_league else None

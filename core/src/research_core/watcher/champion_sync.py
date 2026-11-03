"""Step 3: keep the champion records in step with the platform (design B.6, B.13; A4.9).

Every ten minutes (the Coworld store's meta ``champion_synced_at``) the step reads the
Player's memberships for every league of the active Coworld, not only the leagues the workspace
submitted to (A4.9). A league belongs to the active Coworld when its game's ``coworld_name`` is
the active Coworld's name (from its manifest). The name, not the ``cow_`` id, is the join: every
Coworld version has its own id, leagues follow the canonical version, and the workspace keeps
the id it switched to, so the ids part as soon as a new version ships. Leagues of other
Coworlds are never read, even where the Player has a membership: reading them made one sync
raise a ``champion.changed`` for every such league (2026-11-02).

Two version conditions are never silent. Each writes a notice once and keeps one line in the
Coworld store's meta ``league_versions`` for the briefing's Champion section; neither starts a
wake. Meta ``league_versions_reported`` records what a notice already told:

* no league runs the Coworld's name: the step reports ``nothing_to_sync`` and reads no
  membership; one notice while the condition lasts;
* leagues run another version than the workspace's: the sync still follows them; one notice per
  league version (the Coworld store is per workspace version, so per pair of versions).

For each league:

* a membership flagged ``is_champion`` is the champion: state ``champion``;
* otherwise the Player's newest membership stands for it with its platform status mapped onto
  the champion states (``competing`` is ``placed``; ``pending``, ``processing`` and
  ``rejected`` keep their names);
* no membership at all is ``none``; a league the store never recorded is left alone then,
  because writing ``none`` for an empty league would raise ``champion.changed`` on the first
  tick of every new workspace.

``champion.sync`` writes the row and emits ``champion.changed`` only when the policy version or
the state differs (a change made outside the workspace, or one the submit tool already
recorded); the management wake on that event is the wake step's, which starts one Session for
all pending changes (``batch_by_kind`` in ``handlers.py``). A platform error leaves the
stamp alone so the next tick tries again.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from research_core.platform.errors import PlatformError
from research_core.store import champion, notices
from research_core.store._common import now_iso, parse_iso

if TYPE_CHECKING:  # pragma: no cover
    from research_core.platform.protocol import League, Membership
    from research_core.watcher.context import TickContext

SYNC_INTERVAL = timedelta(minutes=10)
SYNCED_AT = "champion_synced_at"
LEAGUE_VERSIONS = "league_versions"
REPORTED = "league_versions_reported"
NO_LEAGUE = "no_league"
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
    active = platform.coworld_manifest(ctx.coworld.coworld_id)
    leagues = leagues_of(ctx, active["name"])
    _report_versions(ctx, active, leagues)
    if not leagues:
        return {
            "leagues": [],
            "changed": [],
            "nothing_to_sync": f"no league runs the Coworld {active['name']}",
        }
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


def leagues_of(ctx: TickContext, coworld_name: str) -> list[League]:
    """The leagues whose game runs the Coworld ``coworld_name``, in any version."""
    return [league for league in ctx.platform.leagues() if league.coworld_name == coworld_name]


def _report_versions(ctx: TickContext, active: dict[str, Any], leagues: list[League]) -> None:
    """Keep the briefing line current and write each version notice once (module docstring)."""
    store = ctx.store
    name = active["name"]
    workspace = _label(active)
    reported: dict[str, str | None] = json.loads(store.get_meta(REPORTED) or "{}")
    if not leagues:
        line = f"No league runs the Coworld {name}; the workspace is on version {workspace}"
        if NO_LEAGUE not in reported:
            _notice(
                ctx,
                f"No league runs the Coworld {name} (the workspace is on version {workspace}),"
                " so champion sync has nothing to follow. It follows the leagues again once a"
                f" league for {name} exists.",
            )
            reported[NO_LEAGUE] = None
    else:
        reported.pop(NO_LEAGUE, None)
        newer = sorted(
            {league.coworld_id for league in leagues if league.coworld_id} - {active["id"]}
        )
        for coworld_id in newer:
            if coworld_id in reported:
                continue
            label = _league_label(ctx, coworld_id)
            _notice(
                ctx,
                f"A newer version of the Coworld {name} exists: the workspace is on version"
                f" {workspace} and is behind its leagues, which run version {label}. Champion"
                " sync still follows those leagues. The workspace's policies may need checking"
                f" on version {label}.",
            )
            reported[coworld_id] = label
        labels = ", ".join(str(reported[coworld_id]) for coworld_id in newer)
        line = (
            f"The workspace is on {name} version {workspace}; its leagues run {labels}"
            if newer
            else None
        )
    store.set_meta(REPORTED, json.dumps(reported, sort_keys=True))
    if line is None:
        store.delete_meta(LEAGUE_VERSIONS)
    else:
        store.set_meta(LEAGUE_VERSIONS, line)


def _notice(ctx: TickContext, text: str) -> None:
    notices.add(ctx.wstore, ctx.actor, text=text)


def _league_label(ctx: TickContext, coworld_id: str) -> str:
    """The league version as ``<version> (<id>)``; the bare id when its manifest is unreadable."""
    try:
        return _label(ctx.platform.coworld_manifest(coworld_id))
    except PlatformError:
        return coworld_id


def _label(manifest: dict[str, Any]) -> str:
    version = manifest.get("version")
    return f"{version} ({manifest['id']})" if version else manifest["id"]


def _representative(memberships: list[Membership], league_id: str) -> Membership | None:
    """The Player's newest membership in the league, when it has one."""
    in_league = [m for m in memberships if m.league_id == league_id]
    return in_league[-1] if in_league else None

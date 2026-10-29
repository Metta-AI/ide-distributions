"""The ``submit`` tool (design §11, B.6, B.13, H "Submission paths"): Orchestrator only.

Submit takes any uploaded Version. A user-requested submission (``user_requested=true``)
runs at once: no setting, quality bar, pause or Task applies; the result may carry warnings
(not merged, no Experiment, paused, mode ask) and ``cites`` records the user's message (a
missing citation is asked for, never required: A4.5). A submission on the agents' own
initiative (``user_requested=false``) must pass :func:`submission.autonomous_allowed`; the
refusal names every failed reason and says a user request overrides it.

Before the platform call a ``submit`` intent keyed ``<Version>:<league>`` is committed; an
intent found ``pending`` (a crash after the platform accepted it) is reconciled through
``platform.submissions`` and never sent twice. After a submission the league's champion record
is synced from the platform's memberships, the event ``version.submitted`` is written, a notice
tells the user, and the result carries the Version with its computed status.

Workspace executor: it needs the platform client.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from research_core.platform import submission
from research_core.platform.errors import PlatformError
from research_core.platform.protocol import League, Membership, Submission, with_retries
from research_core.store import champion, intents, notices, versions
from research_core.tools import _args as a
from research_core.tools import version as version_tool
from research_core.tools.base import ToolCall, ToolContext, ToolError, ToolSpec, tool

SUBMIT_ROUTE = "POST /v2/league-submissions"
AUTO_CHAMPION = ("always", "never", "lineage")
CITES_NOTE = (
    "the submission went through; record the user's message that asked for it by passing"
    " cites=<message reference> on user-requested submits"
)


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    record = versions.read(ctx.store, version_tool.version_number(call.get("version")))
    vid = record["id"]
    policy_version_id = (record.get("uploaded") or {}).get("policy_version_id")
    if not policy_version_id:
        raise ToolError(
            f"{vid} is not uploaded; call upload first (submit takes any uploaded Version)"
        )
    league = find_league(ctx, call.get("league"))
    user_requested = bool(call.get("user_requested"))
    cites = call.get("cites")
    auto_champion = call.get("auto_champion") or "always"
    reason = call.get("reason")
    warnings: list[str] = []
    if user_requested:
        warnings = submission.shortfalls(ctx, record, league.id, run_check=False)
    else:
        allowed, reasons = submission.autonomous_allowed(ctx, record, league.id)
        if not allowed:
            raise ToolError(
                [f"{vid} may not be submitted to {league.name} on the agents' own initiative"]
                + reasons,
                version=vid,
                league=league.id,
            )
    intent = intents.begin(
        ctx.store, ctx.actor, kind="submit", key=f"{vid}:{league.id}", subject=vid
    )
    # A ``done`` intent is not finished again: its row already records the action.
    pending = intent["state"] == "pending"
    submission.invalidate(policy_version_id)
    platform = ctx.platform()
    reconciled = False
    submitted: Submission | None = None
    if intent["existing"]:
        submitted = find_submission(ctx, policy_version_id, league.id)
        if submitted is not None and not pending:
            return finish_result(ctx, record, league, submitted, already=True)
        if submitted is not None:
            reconciled = True
            finish(ctx, intent, "done", {**asdict(submitted), "reconciled": True})
    if submitted is None:
        try:
            submitted = with_retries(
                lambda: platform.submit(
                    league_id=league.id,
                    policy_version_id=policy_version_id,
                    auto_champion=auto_champion,
                ),
                sleep=version_tool.sleep,
            )
        except PlatformError as error:
            if pending:
                finish(ctx, intent, "failed", error.to_dict())
            raise version_tool.platform_refusal(SUBMIT_ROUTE, error) from error
        if pending:
            finish(ctx, intent, "done", asdict(submitted))
    submission.invalidate(policy_version_id)
    membership = sync_champion(ctx, league, policy_version_id, submitted, reason)
    with ctx.store.tx(ctx.actor) as tx:
        tx.event(
            "version.submitted",
            vid,
            {
                "autonomous": not user_requested,
                "user_requested": user_requested,
                "cites": cites,
                "league": league.id,
                "league_name": league.name,
                "submission_id": submitted.id,
                "policy_version_id": policy_version_id,
                "auto_champion": auto_champion,
                "reason": reason,
                "reconciled": reconciled,
            },
        )
    result = finish_result(ctx, record, league, submitted, membership=membership)
    status = result["status"]
    notices.add(
        ctx.wstore,
        ctx.actor,
        text=f"Submitted {vid} to {league.name} ({status})",
        refs=[vid],
    )
    result["reconciled"] = reconciled
    if warnings:
        result["warnings"] = warnings
    if user_requested and not cites:
        result["note"] = CITES_NOTE
    return result


def find_league(ctx: ToolContext, value: Any) -> League:
    """The league named by id or by name."""
    platform = ctx.platform()
    text = str(value).strip()
    try:
        return with_retries(lambda: platform.league(text), sleep=version_tool.sleep)
    except PlatformError as error:
        if error.status != 404:
            raise version_tool.platform_refusal("GET /v2/leagues/<id>", error) from error
    leagues = version_tool.platform_call("GET /v2/leagues", platform.leagues)
    for league in leagues:
        if league.name.casefold() == text.casefold():
            return league
    known = ", ".join(f"{league.name} ({league.id})" for league in leagues) or "none"
    raise ToolError(f"no league {text!r}; known leagues: {known}")


def find_submission(ctx: ToolContext, policy_version_id: str, league_id: str) -> Submission | None:
    """The platform's newest submission of this policy version to the league, if any."""
    platform = ctx.platform()
    found = version_tool.platform_call(
        "GET /v2/league-submissions",
        lambda: platform.submissions(policy_version_id=policy_version_id, league_id=league_id),
    )
    return found[-1] if found else None


def sync_champion(
    ctx: ToolContext,
    league: League,
    policy_version_id: str,
    submitted: Submission,
    reason: str | None,
) -> Membership | None:
    """Keep the league's champion record in step with the platform after a submission.

    The Player's champion membership wins; otherwise the new submission's membership stands
    as the representative in flight, with the submission's platform state.
    """
    platform = ctx.platform()
    memberships = version_tool.platform_call(
        "GET /v2/league-policy-memberships",
        lambda: platform.memberships(league_id=league.id, mine=True),
    )
    crowned = [m for m in memberships if m.is_champion]
    ours = [m for m in memberships if m.policy_version.id == policy_version_id]
    if crowned:
        chosen, state = crowned[0], "champion"
    elif ours:
        chosen, state = ours[0], ("placed" if ours[0].status == "competing" else submitted.status)
    else:
        return None
    if state not in champion.STATES:
        state = "pending"
    champion.sync(
        ctx.store,
        ctx.actor,
        league_id=league.id,
        division_id=chosen.division_id,
        membership_id=chosen.id,
        policy_version_id=chosen.policy_version.id,
        state=state,
        reason=reason if chosen.policy_version.id == policy_version_id else None,
    )
    return chosen


def finish_result(
    ctx: ToolContext,
    record: dict[str, Any],
    league: League,
    submitted: Submission,
    *,
    membership: Membership | None = None,
    already: bool = False,
) -> dict[str, Any]:
    fresh = version_tool.with_status(ctx, versions.read(ctx.store, record["id"]))
    result: dict[str, Any] = {
        "record": fresh,
        "submission": asdict(submitted),
        "league": {"id": league.id, "name": league.name},
        "status": fresh["submission_status"],
        "champion": champion.read(ctx.store, league.id),
    }
    if membership is not None:
        result["membership"] = asdict(membership)
    if already:
        result["already_submitted"] = True
    return result


def finish(ctx: ToolContext, intent: dict[str, Any], state: str, result: dict[str, Any]) -> None:
    intents.finish(
        ctx.store, ctx.actor, version_tool.intent_number(intent), state=state, result=result
    )


SPEC = tool(
    ToolSpec(
        name="submit",
        description=(
            "Submit an uploaded Version to a league. A user's request (user_requested=true,"
            " cites=<their message>) always runs and may warn; a submission on your own"
            " initiative must pass the submissions settings and the quality bar, and the"
            " refusal names each reason. Syncs the champion record and notifies the user."
        ),
        handler=handle,
        roles=version_tool.ORCHESTRATOR,
        executor="workspace",
        params={
            "version": version_tool.VERSION,
            "league": a.string("The league id (or its name)."),
            "auto_champion": a.string(
                "always (default), never or lineage: whether the platform makes this the"
                " Player's champion once placed.",
                enum=list(AUTO_CHAMPION),
            ),
            "reason": a.string("Why this Version is submitted now (kept on the champion record)."),
            "user_requested": a.boolean(
                "true when the user asked for this submission: no setting or quality bar"
                " applies. false (default) for your own initiative."
            ),
            "cites": a.string("The reference of the user's message that asked for it."),
        },
        required=("version", "league"),
    )
)

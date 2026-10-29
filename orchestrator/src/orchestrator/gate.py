"""Minute supervisor preflight. Only outstanding work wakes the model."""

import hashlib
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from .context import subprocess_context
from .research import Research
from .storage import (
    card_sessions,
    league_results_seen,
    mark_league_result_seen,
    open_storage,
    snapshot,
    submission_sessions,
)

LEAGUE_RESULT_NEXT = (
    "Our champion submission played its first league match. Read the result (and its replay "
    "if useful) and decide the next step yourself: keep it, research a fix, or submit another "
    "version. Act without asking the user, then tell them what happened and what you did."
)


def transitions(state, research):
    pending = []
    default = research
    for run in state["awaiting"]:
        metadata = json.loads(run["metadata"])
        awaiting = metadata.get("awaiting") or {}
        event = {"table": "task_runs", "id": run["id"], "card": run["task_id"]}
        research = default
        if run.get("game"):
            # A card of another game: its experiments live in that game's workspace.
            event["game"] = run["game"]
            research = default.in_game(run["game"])
        if awaiting.get("permit"):
            pending.append({
                **event,
                "kind": "bad_handoff",
                "reason": "awaiting.permit is unsupported; tell the user to answer the ask "
                "and explicitly request a relaunch",
            })
            continue
        if awaiting.get("xp"):
            # xp status takes an experiment ID; handoffs name it separately from request IDs.
            experiment = (metadata.get("experiment") or {}).get("id")
            if not experiment:
                pending.append(
                    {**event, "kind": "bad_handoff", "reason": "awaiting.xp needs experiment.id"}
                )
                continue
            rows = research.xp_status(experiment)
            wanted = set(awaiting["xp"])
            rows = [row for row in rows if row["request_id"] in wanted]
            if (
                rows
                and all(row["state"] in {"completed", "failed"} for row in rows)
                and {row["request_id"] for row in rows} == wanted
            ):
                pending.append(
                    {**event, "kind": "xp_terminal", "experiment": experiment, "requests": rows}
                )
        if awaiting.get("credits"):
            credits = research.credits()
            if credits.get("available") and credits["credits"] > 0:
                pending.append({**event, "kind": "credits_available", "credits": credits})
    # The same key in every status read and wake, so card_create(source_event=...) from a chat
    # turn and from a background wake resolve to one card.
    return [
        {**event, "source_event": f"{event['table']}:{event['id']}:{event['kind']}"}
        for event in pending
    ]


def other_research_games(env):
    """Games other than the default that have a canonical repository on this host."""
    if "RESEARCH_CANONICAL" not in env:
        return []
    canonical = Path(env["RESEARCH_CANONICAL"])
    games = sorted(
        path.name.removeprefix("canonical-").removesuffix(".git")
        for path in canonical.parent.glob("canonical-*.git")
        if path != canonical and (path / "HEAD").exists()
    )
    return [game for game in games if game != env.get("RESEARCH_GAME")]


def mentions(value, needle):
    """Whether ``needle`` is any string inside the JSON value (the result's shape is the
    platform's; our policy version can appear as a seat, a participant or the replay's policy)."""
    if isinstance(value, dict):
        return any(mentions(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(mentions(item, needle) for item in value)
    return value == needle


def first_league_result(context, research, game=None):
    """A ``league_result`` event when our newest accepted champion submission has played its
    first league match, else None. A submission already woken for makes no platform call."""
    submission = research.call("champion", "submitted")
    if submission is None or submission["id"] in league_results_seen(context):
        return None
    read = research.latest_league_result(submission["league_id"], context.env["RESEARCH_PLAYER_ID"])
    if not mentions(read.get("result"), submission["platform_policy_version_id"]):
        return None
    event = {
        "kind": "league_result",
        "champion_submission": submission["id"],
        "submission_id": submission["submission_id"],
        "league_id": submission["league_id"],
        "policy_version_id": submission["platform_policy_version_id"],
        "result": read["result"],
        "next": LEAGUE_RESULT_NEXT,
        "source_event": f"champion_submissions:{submission['id']}:league_result",
    }
    return {**event, "game": game} if game else event


def evaluate(context, research=None, now=None):
    state = snapshot(context)
    now = now or datetime.now(UTC)
    research = research or Research(context)

    pending = transitions(state, research)
    if now.minute % 10 == 0:
        tick = now.replace(second=0, microsecond=0).isoformat()
        # Old "promotion" records are rewritten on the first poll after an upgrade; the
        # coordinator is woken so it tells the user what was migrated.
        migration = research.call("champion", "migrate")
        if migration is not None:
            pending.append({"kind": "records_migrated", "tick": tick, "migration": migration})
        for game in other_research_games(context.env):
            migration = research.in_game(game).call("champion", "migrate")
            if migration is not None:
                pending.append({"kind": "records_migrated", "tick": tick, "game": game, "migration": migration})
        if research.call("champion", "pending") is not None:
            pending.append({"kind": "champion_submission_poll", "tick": tick})
        for game in other_research_games(context.env):
            if research.in_game(game).call("champion", "pending") is not None:
                pending.append({"kind": "champion_submission_poll", "tick": tick, "game": game})
        # Wake once on the first league match after each champion submission.
        for game in [None, *other_research_games(context.env)]:
            result = first_league_result(context, research.in_game(game), game)
            if result is not None:
                pending.append(result)
    # Native notifications alone deliver task events; cron only polls external waits.
    if not pending:
        return {"wakeAgent": False}
    return {"wakeAgent": True, "context": {"pending": pending}}


def wake(context, state):
    """Cron submits a replayable wake to the existing coordinator; it never runs a planner."""
    directory = context.env.get("SOFTMAX_BRIDGE_DIRECTORY")
    if directory is None:
        if state["wakeAgent"]:
            raise RuntimeError("Coordinator bridge is not configured")
        return {"wakeAgent": False}
    binding = json.loads((Path(directory) / "config.json").read_text())
    url = binding["hermes_url"].rstrip("/")
    headers = {
        "Authorization": "Bearer " + binding["service_key"],
        "Content-Type": "application/json",
    }
    if state["wakeAgent"]:
        with closing(open_storage(context.storage_path)) as conn:
            latest = conn.execute("SELECT value FROM coordinator WHERE key='session'").fetchone()
        # A card's events resume the session that created the card, as native notifications
        # do. Only events with no origin (no card, or a card created outside a session) go to
        # the session that most recently called a tool. A league result resumes the session
        # that made its champion submission.
        origins = card_sessions(context)
        submitters = submission_sessions(context)
        sessions = {}
        for event in state["context"]["pending"]:
            session = (
                submitters.get(event.get("champion_submission"))
                or origins.get(event.get("card"))
                or (latest and latest[0])
            )
            if session:
                sessions.setdefault(session, []).append(event)
        for session, pending in sorted(sessions.items()):
            prompt = (
                "Background coordinator wake. Read current status before acting; "
                "these source events may already be handled. Pass an event's source_event to "
                "card_create for the card it needs; a card already made for it is returned. "
                "Your final reply is shown to the user: include meaningful verified news. "
                "Queue completion records this input as handled.\n"
                + json.dumps({**state["context"], "pending": pending}, sort_keys=True)
            )
            identity = "wake:" + hashlib.sha256((session + "\n" + prompt).encode()).hexdigest()
            request = Request(
                url + "/v1/coordinator/inputs",
                data=json.dumps(
                    {"identity": identity, "request": {"session_id": session, "input": prompt}}
                ).encode(),
                headers=headers,
            )
            with urlopen(request, timeout=30) as response:
                json.load(response)
        # Only once the inbox holds the wake: a failed post retries on the next poll.
        for event in state["context"]["pending"]:
            if event["kind"] == "league_result":
                mark_league_result_seen(context, event["champion_submission"])
    # The inbox owns both deduplication and recovery. Each cron tick may launch
    # the oldest abandoned input; duplicate launches cannot claim it twice.
    with urlopen(Request(url + "/v1/coordinator/pending", headers=headers), timeout=30) as response:
        recovery = json.load(response)["pending"]
    if recovery is None:
        return {"wakeAgent": False}
    request = Request(
        url + "/v1/runs",
        data=json.dumps(
            {**recovery["request"], "coordinator_input_id": recovery["identity"]}
        ).encode(),
        headers=headers,
    )
    with urlopen(request, timeout=30) as response:
        return {"wakeAgent": False, "run_id": json.load(response)["run_id"]}


def main():
    context = subprocess_context()
    # Reconcile durable publication intent before polling model-facing work. This is
    # read-only at the platform and runs again after a coordinator or pod restart.
    Research(context).call("workspace", "publication")
    print(json.dumps(wake(context, evaluate(context))))


if __name__ == "__main__":
    main()

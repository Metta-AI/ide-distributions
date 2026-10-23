"""Minute supervisor preflight. Only outstanding work wakes the model."""

import hashlib
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from .context import subprocess_context
from .ide import IDE
from .research import Research
from .storage import open_storage, snapshot


def transitions(state, research, ide):
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
        if awaiting.get("permit"):
            ask = ide.permit(awaiting["permit"])
            body = ask.get("body") or {}
            if body.get("kind") == "permit" and body.get("answer") is not None:
                pending.append(
                    {
                        **event,
                        "kind": "permit_answered",
                        "ask": awaiting["permit"],
                        "answer": body["answer"],
                    }
                )
    return pending


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


def evaluate(context, research=None, ide=None, now=None):
    state = snapshot(context)
    now = now or datetime.now(UTC)
    research = research or Research(context)

    # Credentials are only needed when there is a permit to read.
    class PermitReader:
        def permit(self, ask):
            return IDE(context).permit(ask)

    pending = transitions(state, research, ide or PermitReader())
    if now.minute % 10 == 0:
        tick = now.replace(second=0, microsecond=0).isoformat()
        if research.call("promotion", "pending") is not None:
            pending.append({"kind": "promotion_poll", "tick": tick})
        for game in other_research_games(context.env):
            if research.in_game(game).call("promotion", "pending") is not None:
                pending.append({"kind": "promotion_poll", "tick": tick, "game": game})
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
            origin = conn.execute("SELECT value FROM coordinator WHERE key='session'").fetchone()
        if origin is not None:
            prompt = (
                "Background coordinator wake. Read current status before acting; "
                "these source events may already be handled. Reuse event-derived command IDs. "
                "Send meaningful verified updates with ide_say. Queue completion records this input as handled.\n"
                + json.dumps(state["context"], sort_keys=True)
            )
            identity = "wake:" + hashlib.sha256((origin[0] + "\n" + prompt).encode()).hexdigest()
            request = Request(
                url + "/v1/coordinator/inputs",
                data=json.dumps(
                    {"identity": identity, "request": {"session_id": origin[0], "input": prompt}}
                ).encode(),
                headers=headers,
            )
            with urlopen(request, timeout=30) as response:
                json.load(response)
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
    print(json.dumps(wake(context, evaluate(context))))


if __name__ == "__main__":
    main()

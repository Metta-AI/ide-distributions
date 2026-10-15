"""Minute supervisor preflight. Only outstanding work wakes the model."""

import json
from datetime import UTC, datetime

from .context import subprocess_context
from .ide import IDE
from .research import Research
from .storage import snapshot


def transitions(state, research, ide, check_champion):
    pending = []
    for run in state["awaiting"]:
        metadata = json.loads(run["metadata"])
        awaiting = metadata.get("awaiting") or {}
        event = {"table": "task_runs", "id": run["id"], "card": run["task_id"]}
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
    if check_champion:
        # The shared command compares league state and recovers interrupted promotions.
        before = research.call("records", "summary")["champion"]
        promotion = research.call("promotion", "status")
        after = research.call("records", "summary")["champion"]
        if before != after or (
            promotion.get("conflict") and not (research.control_read() or {}).get("paused")
        ):
            pending.append({"kind": "promotion", "status": promotion})
    return pending


def evaluate(context, research=None, ide=None, now=None):
    state = snapshot(context)
    now = now or datetime.now(UTC)
    research = research or Research(context)

    # Credentials are only needed when there is a permit to read.
    class PermitReader:
        def permit(self, ask):
            return IDE(context).permit(ask)

    pending = transitions(state, research, ide or PermitReader(), now.minute % 10 == 0)
    if not state["events"] and not pending:
        return {"wakeAgent": False}
    return {"wakeAgent": True, "context": {"events": state["events"], "pending": pending}}


def main():
    print(json.dumps(evaluate(subprocess_context())))


if __name__ == "__main__":
    main()

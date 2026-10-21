"""Minute supervisor preflight. Only outstanding work wakes the model."""

import hashlib
import json
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from .context import subprocess_context
from .ide import IDE
from .research import Research
from .storage import open_storage, snapshot


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

    pending = transitions(state, research, ide or PermitReader(), False)
    if now.minute % 10 == 0 and research.call("promotion", "pending") is not None:
        pending.append(
            {"kind": "promotion_poll", "tick": now.replace(second=0, microsecond=0).isoformat()}
        )
    if not state["events"] and not pending:
        return {"wakeAgent": False}
    return {"wakeAgent": True, "context": {"events": state["events"], "pending": pending}}


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
    # Recover the oldest admitted input before considering a new wake. The native
    # inbox retains its identity and sequence even when the accepting process died.
    with urlopen(Request(url + "/v1/coordinator/pending", headers=headers), timeout=30) as response:
        recovery = json.load(response)["pending"]
    if recovery is not None:
        payload = {**recovery["request"], "coordinator_input_id": recovery["identity"]}
        key = "coordinator-recover:" + hashlib.sha256(recovery["identity"].encode()).hexdigest()
    else:
        if not state["wakeAgent"]:
            return {"wakeAgent": False}
        with closing(open_storage(context.storage_path)) as conn:
            origin = conn.execute("SELECT value FROM coordinator WHERE key='session'").fetchone()
        if origin is None:
            return {"wakeAgent": False}
        prompt = (
            "Background coordinator wake. Read current status and board_reconcile before acting; "
            "these source events may already be handled. Reuse event-derived command IDs. "
            "Send meaningful verified updates with ide_say, then acknowledge handled events.\n"
            + json.dumps(state["context"], sort_keys=True)
        )
        payload = {"session_id": origin[0], "input": prompt}
        key = "coordinator-wake:" + hashlib.sha256((origin[0] + "\n" + prompt).encode()).hexdigest()
    # Each failed native run permits one distinct retry. Concurrent cron processes
    # derive the same next key; an uncertain admission always reuses its original key.
    for attempt in range(3):
        with closing(open_storage(context.storage_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = conn.execute(
                "SELECT retry_until, run_id FROM wakes WHERE id=?", (key,)
            ).fetchone()
            if saved is None:
                with urlopen(
                    Request(url + "/v1/capabilities", headers=headers), timeout=30
                ) as response:
                    capability = json.load(response)["features"]["runs_idempotency"]
                if (
                    not capability["supported"]
                    or not capability["durable"]
                    or capability["retention_seconds"] <= 60
                ):
                    raise RuntimeError("Coordinator wakes require durable run admission")
                saved = (time.time() + min(3600, capability["retention_seconds"] - 60), None)
                conn.execute("INSERT INTO wakes(id, retry_until) VALUES (?, ?)", (key, saved[0]))
            # Commit intent before the HTTP effect; exact retries share the durable native key.
            conn.commit()
            if saved[1] is not None:
                with urlopen(
                    Request(url + "/v1/runs/" + saved[1], headers=headers), timeout=30
                ) as response:
                    outcome = json.load(response)
                if outcome["status"] not in {"failed", "interrupted"}:
                    return {"wakeAgent": False, "run_id": saved[1]}
                key = "coordinator-retry:" + hashlib.sha256(saved[1].encode()).hexdigest()
                continue
            if time.time() >= saved[0]:
                raise RuntimeError(
                    "Coordinator wake admission is unconfirmed outside its safe retry window; reconcile the saved key "
                    + key
                )
            request = Request(
                url + "/v1/runs",
                data=json.dumps(payload).encode(),
                headers={**headers, "Idempotency-Key": key},
            )
            with urlopen(request, timeout=30) as response:
                receipt = json.load(response)
            with conn:
                conn.execute("UPDATE wakes SET run_id=? WHERE id=?", (receipt["run_id"], key))
        return {"wakeAgent": False, "run_id": receipt["run_id"]}
    raise RuntimeError("Coordinator wake failed three times; inspect its native run history")


def main():
    context = subprocess_context()
    print(json.dumps(wake(context, evaluate(context))))


if __name__ == "__main__":
    main()

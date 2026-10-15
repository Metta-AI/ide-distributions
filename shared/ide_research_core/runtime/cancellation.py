"""Cancel the card owned by one inbound task through the existing queue controls.

The committed dropped receipt prevents admission, recovery, and late handoff publication.
Queue.stop requests native worker shutdown; the reply does not claim process termination.
The VM, gateway, canonical history, and unrelated cards remain available.
"""

from ide_research_core.comms import delivery, runtime_messages
from ide_research_core.queue.base import Queue
from ide_research_core.runctx import RunContext
from ide_research_core.runtime.admission import _save_receipt, receipt_path
from ide_research_core.runtime.blocked import COACH_STOPPED_PREFIX
from ide_research_core.runtime.ledger import committed_json, ledger_of, now_iso


def cancel_task(ctx: RunContext, queue: Queue, record: dict, message: dict, allow_no_remote: bool):
    """Apply a cancel control under routing's canonical lock, retrying after a crash."""
    from ide_research_core.runtime import routing

    canonical = ctx.canonical
    target = delivery.records(canonical).get(message["task"])
    if target is None or target["kind"] != "task":
        note = runtime_messages.task_cancelled("unknown")
        return routing._answer_with_note(canonical, record, note, allow_no_remote)
    if target["state"] == "acknowledged":
        note = runtime_messages.task_cancelled("closed")
        return routing._answer_with_note(canonical, record, note, allow_no_remote)
    responsible = target["responsible"]
    request_id = responsible["request_id"]
    # A backlog item may share a planning card with other requests. Cancelling it
    # withdraws its message, never stops that shared planner.
    if request_id is not None and not responsible["backlog"]:
        path = receipt_path(ledger_of(canonical), request_id)
        receipt = committed_json(canonical, path)
        if receipt is None:
            receipt = {"request_id": request_id, "history": [], "tasks": []}
        elif receipt["state"] in routing.CLOSED_RECEIPT_STATES and not receipt.get("cancelled_by"):
            note = runtime_messages.task_cancelled("closed")
            return routing._answer_with_note(canonical, record, note, allow_no_remote)
        for task in receipt.get("tasks", []):
            card = queue.get(task["task_id"])
            if card.state in ("pending", "claimed"):
                if card.board_status in (None, "ready", "running"):
                    queue.stop(
                        card.task_id, f"{COACH_STOPPED_PREFIX} the coach cancelled this task"
                    )
                elif card.state == "pending":
                    queue.assign(card.task_id, None)
                else:
                    raise ValueError(f"Cannot request stop for Hermes status {card.board_status}")
        if not receipt.get("cancelled_by"):
            receipt = receipt | {
                "state": "dropped",
                "reason": "The coach cancelled this task",
                "cancelled_by": record["id"],
                "history": [*receipt["history"], {"state": "dropped", "at": now_iso()}],
            }
            _save_receipt(canonical, path, receipt, f"cancel {request_id}")
    note = runtime_messages.task_cancelled("requested")
    reply = routing._reply_once(canonical, record["id"], note, allow_no_remote)
    routing._acknowledge(canonical, target, note.title, reply)
    routing._acknowledge(canonical, record, note.title, reply)
    return [routing.Action("controlled", record["id"], "cancel")]

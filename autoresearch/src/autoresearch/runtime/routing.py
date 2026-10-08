"""Inbound routing: each inbound message gets exactly one responsible card (Overview C11;
System Design 7 Inbound). Runtime only (``queue-keeper``, each run).

``route_all`` takes the canonical lock once (the caller must not hold it) and:

0. **Adopt.** Files an external agent wrote into ``comms/inbox/`` directly are committed when
   valid (``comms.inbound.adopt_direct_writes``); an invalid one gets a runtime ``note`` in
   reply to its id, once, its id is recorded in ``.supervisor/ledger/inbound-rejected/`` and
   is final. A committed inbox entry that does not parse is skipped (action ``unreadable``)
   so one bad file never stops routing.
1. **Receive.** Every committed ``comms/inbox/<id>.json`` without a delivery record gets one
   (``comms.delivery``; state ``received``), with ``thread`` (the thread of the outbound
   message it answers: its ``thread``, else its id) and ``late`` (a reply after that
   message's ``expires``). One commit.
2. **Route** each ``received`` record, one commit per message:

   - ``seen``: nothing to do (the seen ledger was written on submission) -> ``acknowledged``.
   - ``task``: checked again (shape, and its refs must resolve at HEAD). Invalid -> a runtime
     ``note`` with ``in_reply_to`` and the errors, and ``acknowledged`` by it. Valid -> its
     own card request (fields from the task, ``created_by`` the I-id, marker ``task:<I-id>``).
   - ``steer`` urgency ``now`` -> always a new P0 card request (marker ``steer:<I-id>``):
     ``planning`` if it has a ``proposed_topic`` and planning is free, else
     ``investigation``; when planning is owned, the topic becomes its own backlog entry
     (``source: proposed_topic``) for the planning card, while the steer stays with the P0
     card.
   - another ``steer`` with a ``proposed_topic`` -> the planning backlog.
   - a ``reply``, or another ``steer``, on a thread with an open card -> that card; else the
     planning backlog.

   A ``task`` in mode planning while another planning card owns planning goes to the
   backlog instead (single planning owner).
3. **Resolve.** A record routed to a request gets the request's task id once admitted, and
   the id is added to that card's ``runtime/cards/<task>.json["responsible_inbound"]``. An
   untaken backlog entry is bound to the open planning card (one not yet claimed, or one
   whose card file already lists it); so is an untaken proposed-topic entry
   (``taken_by``). A record whose responsible request closed without
   acknowledging it (rejected, superseded, dropped, published) goes back to the backlog; a
   ``task`` whose request was rejected for another reason than planning ownership gets a
   runtime note with the reason instead.

Card requests are written to canonical ``cards/requests/`` and committed with the delivery
record, as the runtime's other card requests are (admission picks them up). The marker is in
the request's ``why`` (``[task:<I-id>]``), so a rerun after a crash before the commit finds
the uncommitted request instead of writing a second one. A note is found again in the
outbox by its ``in_reply_to``. Thus a rerun creates no duplicate requests or notes.

The planning backlog is one file per entry, ``.supervisor/ledger/backlog/<I-id>.json``
(C4: no shared append-only files); ``planning_backlog`` lists the open entries for a
planning briefing. ``inbound_for_briefing`` gives the inbound a card's briefing shows.

Taken entries. A ``proposed_topic`` entry, and a ``request`` entry (a planning request that
admission rejected because planning was owned, ``backlog_request``; file
``backlog/<Q-id>.json``), is taken by one planning card (``taken_by``) and then hidden from
other planning briefings. When the taking card's request is dropped, superseded or rejected
the entry is released (``taken_by: null``) for the next planning card.

Acknowledgement happens at publication (``comms.publisher.acknowledge_inbound``).
"""

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from autoresearch.comms import delivery
from autoresearch.comms.envelope import FROM_RUNTIME
from autoresearch.comms.inbound import (
    adopt_direct_writes,
    inbound_problems,
    is_late,
    parse_time,
)
from autoresearch.comms.publisher import (
    commit_and_push,
    find_reply,
    message_id_of_seq,
    publish_runtime,
)
from autoresearch.comms.reader import get as get_published
from autoresearch.comms.reader import read_since
from autoresearch.comms.resolver import ResolveError, resolve
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.kb.requests import bracketed, has_marker
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import REQUESTS_DIR, write_request
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.admission import PLANNING_OWNED, PLANNING_OWNING_STATES
from autoresearch.runtime.ledger import canonical_lock, now_iso

URGENT = "now"
CLOSED_RECEIPT_STATES = ("rejected", "published", "superseded", "dropped")
# A taken backlog entry goes back to the backlog when its taker's request ends in one of these.
RELEASING_STATES = ("rejected", "superseded", "dropped")
TAKEN_SOURCES = ("proposed_topic", "request")
TITLE_LIMIT = 60
SUMMARY_LIMIT = 160
NO_ONE = {"task_id": None, "request_id": None, "backlog": False}
BACKLOG = {"task_id": None, "request_id": None, "backlog": True}


@dataclass
class Action:
    """One thing ``route_all`` did: ``adopted`` (a direct inbox write committed), ``received``,
    ``card_requested``, ``routed_to_card``,
    ``backlogged``, ``rejected`` (a task answered with a note), ``write_rejected`` (a direct
    inbox write answered with a note),
    ``acknowledged`` (a seen), ``unreadable`` (a committed inbox entry that does not parse),
    ``bound`` (a record got its task id) or ``returned`` (back to the backlog)."""

    kind: str
    inbound_id: str
    detail: str = ""
    request_id: str | None = None
    task_id: str | None = None


def _checkpoint(step: str) -> None:
    """Test seam: crash-injection tests replace this to stop after ``step``
    (``received_written``, ``request_written``, ``routed_written``, ``note_published``)."""


# --- reading ---


def _one_line(text: str, limit: int) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _describe(message: dict) -> str:
    """The full text of an inbound message for a briefing."""
    kind = message["kind"]
    if kind == "reply":
        parts = [f"Reply to {message['in_reply_to']}."]
        if message.get("choice"):
            parts.append(f"Choice: {message['choice']}.")
        if message.get("text"):
            parts.append(str(message["text"]))
        return " ".join(parts)
    if kind == "steer":
        text = f"Steer (urgency {message['urgency']}): {message['text']}"
        topic = message.get("proposed_topic")
        if isinstance(topic, dict):
            text += (
                f"\n\nProposed topic: {topic.get('title')} — {topic.get('objective')} "
                f"(priority {topic.get('priority')})"
            )
        return text
    if kind == "task":
        return (
            f"Task: {message['title']} (mode {message['mode']}, priority "
            f"{message['priority']}). Why: {message['why']}"
        )
    return f"{kind}: {message.get('ids')}"


def _committed_json_dir(canonical: Path, directory: str) -> list[dict]:
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{directory}/")
    return [
        json.loads(gitops.show(canonical, "HEAD", path))
        for path in sorted(listing.splitlines())
        if path.endswith(".json")
    ]


def _receipts(canonical: Path) -> dict[str, dict]:
    """Q-id -> request receipt committed at HEAD."""
    return {
        receipt["request_id"]: receipt
        for receipt in _committed_json_dir(canonical, ".supervisor/ledger/requests")
    }


def _committed_requests(canonical: Path) -> list[dict]:
    return _committed_json_dir(canonical, REQUESTS_DIR)


def _current_task(receipt: dict | None) -> str | None:
    tasks = (receipt or {}).get("tasks") or []
    return tasks[-1]["task_id"] if tasks else None


def _answered_message(canonical: Path, message: dict) -> dict | None:
    """The published outbound message an inbound answers (``in_reply_to``), if any."""
    target = message.get("in_reply_to")
    if not isinstance(target, str) or not target.startswith("M-"):
        return None
    try:
        return get_published(canonical, target)
    except LookupError:
        return None


def _thread_of(outbound: dict) -> str:
    return outbound.get("thread") or outbound["id"]


# --- writing ---


def _card_file(ctx: RunContext, task_id: str) -> Path:
    return runtime_dir(ctx) / "cards" / f"{task_id}.json"


def _add_to_card_file(ctx: RunContext, task_id: str, inbound_id: str) -> None:
    """List ``inbound_id`` in the card's ``responsible_inbound`` (if the card file exists; if
    not, admission adds it from the delivery records when it writes the file)."""
    path = _card_file(ctx, task_id)
    if not path.exists():
        return
    meta = read_json(path)
    responsible = list(meta.get("responsible_inbound", []))
    if inbound_id not in responsible:
        write_json(path, meta | {"responsible_inbound": [*responsible, inbound_id]})


def _uncommitted_request(canonical: Path, marker: str) -> str | None:
    """A request with ``marker`` already written in ``cards/requests`` (a crash before its
    commit left it there)."""
    for path in sorted(Layout(canonical).card_requests.glob("Q-*.json")):
        spec = read_json(path).get("spec") or {}
        if has_marker(spec, marker):
            return path.stem
    return None


def request_card(canonical: Path, marker: str, spec: CardSpec) -> Path:
    """Find the request carrying ``marker`` in ``cards/requests``, or write it (``marker`` is
    appended to ``spec.why`` in brackets); return its path. The caller commits it."""
    request_id = _uncommitted_request(canonical, marker)
    if request_id is None:
        spec.why = f"{spec.why} {bracketed(marker)}"
        request_id = write_request(Layout(canonical), "create", spec=spec)
    return Layout(canonical).card_requests / f"{request_id}.json"


def _route_to_request(
    canonical: Path, record: dict, marker: str, spec: CardSpec, extra: list[Path] = ()
) -> list[Action]:
    """Route ``record`` to its own card request; ``extra`` paths go in the same commit."""
    request = request_card(canonical, marker, spec)
    _checkpoint("request_written")
    responsible = NO_ONE | {"request_id": request.stem}
    routed = delivery.moved(
        record, "routed", note=f"card request {request.stem}", responsible=responsible
    )
    path = delivery.write_record(canonical, routed)
    _checkpoint("routed_written")
    commit_and_push(
        canonical, [request, path, *extra], f"inbound {record['id']}: card request {request.stem}"
    )
    return [Action("card_requested", record["id"], marker, request_id=request.stem)]


def _write_backlog_entry(canonical: Path, message: dict) -> Path:
    entry = {
        "id": message["id"],
        "source": "inbound",
        "kind": message["kind"],
        "author": message.get("author"),
        "summary": _one_line(_describe(message), SUMMARY_LIMIT),
        "proposed_topic": message.get("proposed_topic"),
        "created_at": now_iso(),
    }
    path = delivery.backlog_path(canonical, message["id"])
    write_json(path, entry)
    return path


def _write_topic_entry(canonical: Path, message: dict) -> Path:
    """A backlog entry for the ``proposed_topic`` of an urgent steer whose own card cannot be
    the planning card (planning is owned). The steer stays with its P0 card; only the topic
    waits for planning. ``taken_by``: the planning card that takes it (``_resolve``)."""
    topic = message["proposed_topic"]
    entry = {
        "id": message["id"],
        "source": "proposed_topic",
        "kind": message["kind"],
        "author": message.get("author"),
        "summary": _one_line(
            f"Proposed topic: {topic.get('title')} — {topic.get('objective')}", SUMMARY_LIMIT
        ),
        "proposed_topic": topic,
        "taken_by": None,
        "created_at": now_iso(),
    }
    path = delivery.backlog_path(canonical, message["id"])
    write_json(path, entry)
    return path


def backlog_request(canonical: Path, spec: CardSpec, reason: str) -> Path:
    """Write the planning-backlog entry for a planning request admission rejected because
    planning was owned (C11: merged into the backlog, not a second card). The caller commits
    it, under the canonical lock."""
    entry = {
        "id": spec.request_id,
        "source": "request",
        "kind": "planning",
        "author": spec.created_by,
        "summary": _one_line(f"{spec.title}: {spec.why}", SUMMARY_LIMIT),
        "proposed_topic": None,
        "request": {"title": spec.title, "why": spec.why, "refs": list(spec.refs)},
        "reason": reason,
        "taken_by": None,
        "created_at": now_iso(),
    }
    path = delivery.backlog_path(canonical, spec.request_id)
    write_json(path, entry)
    return path


def _backlog_record(canonical: Path, record: dict, message: dict, note: str) -> list[Path]:
    """Write ``record`` routed to the planning backlog and its backlog entry; return paths."""
    routed = delivery.moved(record, "routed", note=note, responsible=dict(BACKLOG))
    return [_write_backlog_entry(canonical, message), delivery.write_record(canonical, routed)]


def _route_to_backlog(canonical: Path, record: dict, message: dict, note: str) -> list[Action]:
    paths = _backlog_record(canonical, record, message, note)
    _checkpoint("routed_written")
    commit_and_push(canonical, paths, f"inbound {record['id']}: planning backlog")
    return [Action("backlogged", record["id"], note)]


def _answer_with_note(
    canonical: Path, record: dict, title: str, body: str, allow_no_remote: bool
) -> list[Action]:
    """Publish (once) a runtime note answering the inbound, and acknowledge it with that."""
    message_id = find_reply(canonical, record["id"], FROM_RUNTIME)
    if message_id is None:
        result = publish_runtime(
            canonical,
            "note",
            title,
            body,
            [],
            allow_no_remote=allow_no_remote,
            in_reply_to=record["id"],
        )
        message_id = message_id_of_seq(canonical, result.seq)
    _checkpoint("note_published")
    answered = delivery.moved(
        record, "acknowledged", note=title, responsible=dict(NO_ONE), acknowledged_by=message_id
    )
    path = delivery.write_record(canonical, answered)
    commit_and_push(canonical, [path], f"inbound {record['id']}: answered by {message_id}")
    return [Action("rejected", record["id"], body)]


# --- routing rules ---


def planning_owned(canonical: Path, queue: Queue) -> bool:
    """Another planning card is pending, claimed, finished-unpublished, or requested and not
    yet admitted (C11)."""
    for state in ("pending", "claimed"):
        if any(card.spec.mode == "planning" for card in queue.list(state)):
            return True
    receipts = _receipts(canonical)
    if any(
        receipt.get("mode") == "planning" and receipt["state"] in PLANNING_OWNING_STATES
        for receipt in receipts.values()
    ):
        return True
    return any(
        (request.get("spec") or {}).get("mode") == "planning" and request["id"] not in receipts
        for request in _committed_requests(canonical)
    )


def _task_problems(canonical: Path, message: dict) -> list[str]:
    payload = {key: message.get(key) for key in ("title", "mode", "why", "refs", "priority")}
    problems = inbound_problems(canonical, "task", payload, message.get("author"))
    if problems:
        return problems
    for uri in message["refs"]:
        try:
            resolve(canonical, uri)
        except ResolveError as error:
            problems.append(f"refs: {uri} does not resolve at canonical HEAD: {error}")
    return problems


def _open_card_on_thread(
    canonical: Path, queue: Queue, record: dict
) -> tuple[str | None, str] | None:
    """(task id or None, request id) of an open card on the record's thread: one that
    published a message on it, or one responsible for another inbound on it. Open means
    requested and not yet admitted, or admitted and not yet claimed (a claimed worker
    would never see the new message). The most recent such card wins."""
    thread = record.get("thread")
    if thread is None:
        return None
    receipts = _receipts(canonical)
    by_task = {
        task["task_id"]: request_id
        for request_id, receipt in receipts.items()
        for task in receipt.get("tasks", [])
    }
    pending = {card.task_id for card in queue.list("pending")}
    candidates: list[tuple[datetime.datetime, str]] = []  # (when it joined the thread, Q-id)
    for other in delivery.records(canonical).values():
        responsible = other["responsible"]
        if (
            other["id"] != record["id"]
            and other.get("thread") == thread
            and other["state"] in ("routed", "acknowledged")
            and responsible["request_id"]
        ):
            candidates.append((parse_time(other["received_at"]), responsible["request_id"]))
    for message in _published_on_thread(canonical, thread):
        if message.get("card") in by_task:
            candidates.append((parse_time(message["created"]), by_task[message["card"]]))
    for _, request_id in sorted(candidates, reverse=True):
        receipt = receipts.get(request_id)
        if receipt is None:
            return None, request_id
        task_id = _current_task(receipt)
        if receipt["state"] == "admitted" and task_id in pending:
            return task_id, request_id
    return None


def _published_on_thread(canonical: Path, thread: str) -> list[dict]:
    return [
        message
        for message in read_since(canonical, "routing", after=0, include_unknown=True)
        if _thread_of(message) == thread
    ]


def _route_to_thread_or_backlog(
    ctx: RunContext, queue: Queue, record: dict, message: dict
) -> list[Action]:
    canonical = ctx.canonical
    found = _open_card_on_thread(canonical, queue, record)
    if found is None:
        return _route_to_backlog(canonical, record, message, "no open card on its thread")
    task_id, request_id = found
    responsible = NO_ONE | {"task_id": task_id, "request_id": request_id}
    routed = delivery.moved(
        record, "routed", note=f"open card {task_id or request_id}", responsible=responsible
    )
    path = delivery.write_record(canonical, routed)
    if task_id is not None:
        _add_to_card_file(ctx, task_id, record["id"])
    _checkpoint("routed_written")
    commit_and_push(canonical, [path], f"inbound {record['id']}: card {task_id or request_id}")
    return [Action("routed_to_card", record["id"], request_id=request_id, task_id=task_id)]


def _steer_spec(message: dict, mode: str) -> CardSpec:
    return CardSpec(
        request_id="",  # write_request assigns the id
        title=f"Steer {message['id']}: {_one_line(message['text'], TITLE_LIMIT)}",
        mode=mode,
        why=f"Urgent steer from {message['author']}: {_one_line(message['text'], 400)}",
        refs=[],
        created_by=message["id"],
        priority="P0",
    )


def _task_spec(message: dict) -> CardSpec:
    return CardSpec(
        request_id="",
        title=message["title"],
        mode=message["mode"],
        why=message["why"],
        refs=list(message["refs"]),
        created_by=message["id"],
        priority=message["priority"],
    )


def _route_one(
    ctx: RunContext, queue: Queue, record: dict, message: dict, allow_no_remote: bool
) -> list[Action]:
    canonical, kind, inbound_id = ctx.canonical, record["kind"], record["id"]
    if kind == "seen":
        done = delivery.moved(record, "acknowledged", note="seen recorded")
        commit_and_push(
            canonical, [delivery.write_record(canonical, done)], f"inbound {inbound_id}: seen"
        )
        return [Action("acknowledged", inbound_id)]
    if kind == "task":
        problems = _task_problems(canonical, message)
        if problems:
            body = f"Inbound task {inbound_id} was not turned into a card:\n\n" + "\n".join(
                f"- {problem}" for problem in problems
            )
            title = f"Task {inbound_id} rejected"
            return _answer_with_note(canonical, record, title, body, allow_no_remote)
        if message["mode"] == "planning" and planning_owned(canonical, queue):
            return _route_to_backlog(canonical, record, message, "planning is owned")
        return _route_to_request(canonical, record, f"task:{inbound_id}", _task_spec(message))
    if kind == "steer" and message.get("urgency") == URGENT:
        extra: list[Path] = []
        mode = "planning" if message.get("proposed_topic") else "investigation"
        if mode == "planning" and planning_owned(canonical, queue):
            mode = "investigation"
            extra.append(_write_topic_entry(canonical, message))
        spec = _steer_spec(message, mode)
        return _route_to_request(canonical, record, f"steer:{inbound_id}", spec, extra)
    if kind == "steer" and message.get("proposed_topic"):
        return _route_to_backlog(canonical, record, message, "proposed topic")
    return _route_to_thread_or_backlog(ctx, queue, record, message)


# --- receive, resolve ---


def _reject_direct_writes(
    canonical: Path, rejected: dict[str, list[str]], allow_no_remote: bool
) -> list[Action]:
    """Answer each newly rejected direct inbox write with one runtime note in reply to its
    id, then record the rejection (``delivery.write_rejection``): the id is final. The file
    is never committed, so it gets no delivery record. A note found already (a crash before
    the record) is reused."""
    actions, paths = [], []
    for inbound_id, problems in sorted(rejected.items()):
        message_id = find_reply(canonical, inbound_id, FROM_RUNTIME)
        body = (
            f"The inbound message {inbound_id} in comms/inbox/ is not valid and was not "
            "delivered: " + "; ".join(problems) + ". Write a corrected message with a new id."
        )
        if message_id is None:
            result = publish_runtime(
                canonical,
                "note",
                f"Inbound {inbound_id} rejected",
                body,
                [],
                allow_no_remote=allow_no_remote,
                in_reply_to=inbound_id,
            )
            message_id = message_id_of_seq(canonical, result.seq)
            actions.append(Action("write_rejected", inbound_id, body))
        paths.append(delivery.write_rejection(canonical, inbound_id, problems, message_id))
    if paths:
        commit_and_push(canonical, paths, f"inbound: {len(paths)} direct writes rejected")
    return actions


def _receive(canonical: Path, inbox: dict[str, dict]) -> list[Action]:
    existing = delivery.records(canonical)
    paths, actions = [], []
    for inbound_id, message in inbox.items():
        if inbound_id in existing:
            continue
        outbound = _answered_message(canonical, message)
        thread = _thread_of(outbound) if outbound else message.get("thread")
        late = (
            bool(outbound)
            and message["kind"] == "reply"
            and is_late(outbound, message["received_at"])
        )
        paths.append(delivery.write_record(canonical, delivery.new_record(message, late, thread)))
        actions.append(Action("received", inbound_id))
    _checkpoint("received_written")
    if paths:
        commit_and_push(canonical, paths, f"inbound: received {len(paths)}")
    return actions


def _open_planning_task(ctx: RunContext, queue: Queue, inbound_id: str) -> tuple[str, str] | None:
    """(task id, request id) of the planning card that takes a backlog entry: an admitted
    planning card whose card file lists it, else one not yet claimed."""
    pending = {card.task_id for card in queue.list("pending")}
    fallback = None
    for request_id, receipt in _receipts(ctx.canonical).items():
        task_id = _current_task(receipt)
        if receipt.get("mode") != "planning" or receipt["state"] != "admitted" or not task_id:
            continue
        path = _card_file(ctx, task_id)
        if path.exists() and inbound_id in read_json(path).get("responsible_inbound", []):
            return task_id, request_id
        if task_id in pending and fallback is None:
            fallback = (task_id, request_id)
    return fallback


def _closed(
    ctx: RunContext, record: dict, receipt: dict, message: dict, allow_no_remote: bool
) -> tuple[list[Path], list[Action]]:
    """A record whose responsible request closed without acknowledging it."""
    canonical = ctx.canonical
    reason = receipt.get("reason", "")
    if record["kind"] == "task" and receipt["state"] == "rejected" and PLANNING_OWNED not in reason:
        title = f"Task {record['id']} rejected"
        body = f"Admission rejected the card for inbound task {record['id']}: {reason}"
        return [], _answer_with_note(canonical, record, title, body, allow_no_remote)
    note = f"request {receipt['request_id']} {receipt['state']} without acknowledging it"
    return _backlog_record(canonical, record, message, note), [
        Action("returned", record["id"], note, request_id=receipt["request_id"])
    ]


def _resolve(
    ctx: RunContext, queue: Queue, inbox: dict[str, dict], allow_no_remote: bool
) -> list[Action]:
    canonical = ctx.canonical
    receipts = _receipts(canonical)
    paths, actions = [], []
    for inbound_id, record in delivery.records(canonical).items():
        if record["state"] != "routed":
            continue
        responsible = record["responsible"]
        receipt = receipts.get(responsible["request_id"]) if responsible["request_id"] else None
        closed = receipt is not None and receipt["state"] in CLOSED_RECEIPT_STATES
        if closed:
            if inbound_id in inbox:  # an unreadable entry is reported, not resolved
                written, done = _closed(ctx, record, receipt, inbox[inbound_id], allow_no_remote)
                paths += written
                actions += done
            continue
        if receipt is not None:
            task_id = _current_task(receipt)
            bound = (task_id, responsible["request_id"])
        elif responsible["backlog"] and responsible["request_id"] is None:
            bound = _open_planning_task(ctx, queue, inbound_id)
        else:
            continue
        if bound is None or bound[0] is None:
            continue
        task_id, request_id = bound
        _add_to_card_file(ctx, task_id, inbound_id)
        if (responsible["task_id"], responsible["request_id"]) == (task_id, request_id):
            continue
        updated = record | {
            "responsible": responsible | {"task_id": task_id, "request_id": request_id}
        }
        paths.append(delivery.write_record(canonical, updated))
        actions.append(Action("bound", inbound_id, request_id=request_id, task_id=task_id))
    taker_state = {
        task["task_id"]: receipt["state"]
        for receipt in receipts.values()
        for task in receipt.get("tasks", [])
    }
    for inbound_id, entry in delivery.backlog_entries(canonical).items():
        if entry.get("source") not in TAKEN_SOURCES:
            continue
        taker = entry.get("taken_by")
        if taker is not None and taker_state.get(taker) in RELEASING_STATES:
            path = delivery.backlog_path(canonical, inbound_id)
            write_json(path, entry | {"taken_by": None})
            paths.append(path)
            actions.append(Action("topic_released", inbound_id, task_id=taker))
            continue
        if taker is not None:
            continue
        bound = _open_planning_task(ctx, queue, inbound_id)
        if bound is None:
            continue
        path = delivery.backlog_path(canonical, inbound_id)
        write_json(path, entry | {"taken_by": bound[0]})
        paths.append(path)
        actions.append(Action("topic_taken", inbound_id, request_id=bound[1], task_id=bound[0]))
    if paths:
        commit_and_push(canonical, paths, f"inbound: {len(actions)} bound or returned")
    return actions


def route_all(
    ctx: RunContext,
    queue: Queue,
    allow_no_remote: bool = False,
    inbox_writers: frozenset[int] | None = None,
    adopt: bool = True,
) -> list[Action]:
    """Receive, route and resolve every inbound message (C11); see the module docstring.
    Takes the canonical lock: the caller must not hold it. Idempotent; safe to rerun after
    a crash at any point. ``allow_no_remote`` is for runtime notes (tests, local runs);
    ``inbox_writers`` restricts adoption to files those uids own (``comms.inbound``);
    ``adopt=False`` skips adoption (the writer list could not be resolved)."""
    canonical = ctx.canonical
    actions: list[Action] = []
    with canonical_lock(canonical):
        if adopt:
            adoption = adopt_direct_writes(canonical, inbox_writers)
            actions += [Action("adopted", inbound_id) for inbound_id in adoption.adopted]
            actions += _reject_direct_writes(canonical, adoption.rejected, allow_no_remote)
        skipped: list[str] = []
        inbox = delivery.inbox(canonical, skipped)
        actions += [Action("unreadable", path) for path in skipped]
        actions += _receive(canonical, inbox)
        for inbound_id, record in delivery.records(canonical).items():
            if record["state"] == "received" and inbound_id in inbox:
                actions += _route_one(ctx, queue, record, inbox[inbound_id], allow_no_remote)
        actions += _resolve(ctx, queue, inbox, allow_no_remote)
    return actions


# --- briefings ---


def _briefing_entry(message: dict, record: dict, responsible_task: str | None) -> dict:
    return {
        "id": message["id"],
        "kind": message["kind"],
        "urgency": message.get("urgency"),
        "author": message.get("author"),
        "body": _describe(message),
        "late": record["late"],
        "responsible_task": responsible_task,
    }


def inbound_for_briefing(
    canonical: Path, task_id: str, request_id: str | None = None, planning: bool = False
) -> list[dict]:
    """``BriefingInputs.inbound`` for card ``task_id``: the unacknowledged inbound it is
    responsible for (routed to the task or to ``request_id``; with ``planning``, also the
    backlog no card has taken yet, as admission adds it to a not-yet-claimed planning card),
    in full, and every other urgent (``steer`` now) inbound, which the briefing shows
    read-only as handled by its responsible card."""
    inbox = delivery.inbox(canonical)
    mine = set(delivery.responsible_ids(canonical, task_id, request_id, planning))
    entries = []
    for inbound_id, record in delivery.records(canonical).items():
        message = inbox.get(inbound_id)
        if message is None or record["state"] != "routed":
            continue
        if inbound_id in mine:
            entries.append(_briefing_entry(message, record, task_id))
        elif message.get("urgency") == URGENT:
            responsible = record["responsible"]
            other = responsible["task_id"] or responsible["request_id"] or "the planning card"
            entries.append(_briefing_entry(message, record, other))
    return entries


def planning_backlog(canonical: Path, task_id: str | None = None) -> list[str]:
    """``BriefingInputs.planning_backlog``: one line per open backlog entry. For planning card
    ``task_id``, inbound messages it is responsible for are left out (its briefing shows them
    in full under the inbound messages), and proposed topics another planning card took are
    left out too. Without ``task_id``, every open entry."""
    records = delivery.records(canonical)
    shown = set(delivery.responsible_ids(canonical, task_id, planning=True)) if task_id else set()
    lines = []
    for inbound_id, entry in delivery.backlog_entries(canonical).items():
        record = records.get(inbound_id)
        if entry.get("source") in TAKEN_SOURCES:
            if entry.get("taken_by") not in (None, task_id):
                continue
        elif (
            record is None
            or record["state"] == "acknowledged"
            or not record["responsible"]["backlog"]
            or inbound_id in shown
        ):
            continue
        lines.append(f"{inbound_id} ({entry['kind']} from {entry['author']}): {entry['summary']}")
    return lines

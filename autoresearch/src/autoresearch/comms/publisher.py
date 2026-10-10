"""Crash-safe outbox publication (Overview C5). Runtime only.

Files (all in canonical):

- ``comms/staged/<M-id>.json``: staged by a card worker; arrives in canonical with the merge.
- ``comms/outbox/<seq:08d>-<M-id>.json``: the published, immutable message.
- ``.supervisor/ledger/published/<M-id>.json``: the publication receipt
  ``{id, seq, file, published_rev, from, published_at}``.

Sequence numbers. ``next_seq = 1 + max(seq of the files in comms/outbox/)``: no counter, only
the outbox files themselves. Publishing one message writes, in this order, the outbox file,
the receipt, and (for a staged message) the removal of the staged file; one commit then
covers every message of the call, and a push of ``main`` to ``origin`` follows. A failed
push keeps the local commit, and every later publication pushes again; the result reports
``pushed`` so queue-keeper can raise a help_request on repeated failures (Task 3.9). A
missing ``origin`` is a configuration error (``PublishError``) unless the caller passes
``allow_no_remote=True`` (tests, local runs).

The ``@result`` rev of a staged message is the first-parent commit of canonical that added
its staged file (normally the merge commit of its card), not simply the merge being
published now: a message left over from an earlier merge keeps that merge's revision. A
staged message that fails the envelope is skipped (left staged) and reported in the result;
the rest still publish.

Recovery (``recover``, run first by every publication): an outbox file with no receipt gets
its receipt; a receipt with no outbox file gets the file rebuilt from the staged message; a
staged file with a receipt is removed; then the leftovers are committed. Because the outbox
file is written first and recovery completes it before any new number is taken, each message
gets exactly one sequence number across crashes, with no gaps.

Readers see only committed outbox files (``reader``), never a half-done publication.

Acknowledgement (C11). ``publish_hook`` then moves the inbound the publishing card is
responsible for (``comms.delivery`` records) to ``acknowledged``: a ``reply``/``steer`` by
the card's published ``note`` with ``in_reply_to``, a ``task`` by a runtime note published
here (found again in the outbox on a rerun, so it is published once). That note quotes the
card's completion summary (``runtime_messages.task_acknowledgement``). A task acknowledged
``blocked`` gets one follow-up note when its work finishes (``acknowledge_inbound``).

Runtime messages. Runtime code publishes only ``runtime_messages.RuntimeMessage``s, through
``publish_message``; ``publish_runtime`` is the free-text primitive under it.

Locking. Every function here must run under ``canonical.lock`` held by the caller, and never
takes it: ``process_handoffs`` calls ``publish_hook`` while it holds the (non-reentrant) lock.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from autoresearch.comms import delivery, kinds, runtime_messages
from autoresearch.comms.envelope import (
    FROM_AGENT,
    FROM_RUNTIME,
    RESULT_REV,
    MessageProblems,
    build_message,
    check_message,
    now_iso,
    ref_dict,
)
from autoresearch.comms.runtime_messages import RuntimeMessage
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.records.registry import entity_of_id
from autoresearch.runtime import provenance
from autoresearch.runtime.admission import receipt_path as request_receipt_path
from autoresearch.runtime.ledger import committed_json, ledger_of
from autoresearch.runtime.markers import MAX_REPAIR_CHAIN, blocked_marker_path

OUTBOX_DIR = "comms/outbox"
INBOX_DIR = "comms/inbox"
SUMMARY_LIMIT = 600  # characters of a card's summary quoted in its task acknowledgement
REFERENCED_RECORDS = 10  # journal and created records a task acknowledgement refers to
# Card outcomes that end a task's work; ``blocked`` does not (a task acknowledged blocked gets
# a follow-up acknowledgement with one of these).
FINAL_OUTCOMES = ("done", "partial", "dropped", "failed")
# Repairs of repairs followed from a blocked card to the card that finishes its work: as deep
# as ``runtime.blocked`` repairs (``MAX_REPAIR_CHAIN``), so the last repair card is reached.
REPAIR_HOPS = MAX_REPAIR_CHAIN
# CardSpec.created_by of a runtime request; equal to ``runtime.planning.RUNTIME``, which cannot be
# imported here (planning -> routing -> publisher is an import cycle).
CREATED_BY_RUNTIME = "runtime"
STAGED_DIR = "comms/staged"
RECEIPTS_DIR = ".supervisor/ledger/published"
REMOTE = "origin"
BRANCH = "main"
OUTBOX_NAME = re.compile(r"(?P<seq>\d{8})-(?P<id>M-[0-9A-HJKMNP-TV-Z]{26})\.json")


class PublishError(RuntimeError):
    """Publication cannot proceed: no ``origin`` remote (and none allowed), or state that
    recovery cannot repair (a receipt whose message is lost)."""


@dataclass
class PublishResult:
    seqs: list[int] = field(default_factory=list)  # new sequence numbers, in order
    pushed: bool = False  # main reached origin (False also when no remote was allowed)
    skipped: dict[str, list[str]] = field(default_factory=dict)  # invalid staged M-id -> problems

    @property
    def seq(self) -> int:
        """The single sequence number of a ``publish_runtime`` call."""
        (seq,) = self.seqs
        return seq


def _checkpoint(step: str) -> None:
    """Test seam: crash-injection tests replace this to stop after ``step``
    (``outbox_written``, ``receipt_written``, ``staged_removed``, ``before_commit``,
    ``before_push``, ``ack_published``, ``final_ack_published``)."""


# --- files ---


def outbox_name(seq: int, message_id: str) -> str:
    return f"{seq:08d}-{message_id}.json"


def _outbox_files(canonical: Path) -> dict[str, tuple[int, Path]]:
    """Message id -> (seq, path) for the outbox files on disk."""
    found = {}
    for path in sorted((canonical / OUTBOX_DIR).glob("*.json")):
        match = OUTBOX_NAME.fullmatch(path.name)
        if match:
            found[match["id"]] = (int(match["seq"]), path)
    return found


def already_published(canonical: Path, message: RuntimeMessage) -> bool:
    """True if the outbox holds a message with ``message``'s ``runtime_key``, or (published
    before runtime_key existed) a title containing its ``legacy_title``. Runtime jobs use it to
    publish a help_request or note once, also when a crash lands between publishing and
    committing their own dedupe marker (``runtime_messages``)."""
    for _seq, path in _outbox_files(Path(canonical)).values():
        published = read_json(path)
        if not isinstance(published, dict):
            continue
        if message.key is not None and published.get("runtime_key") == message.key:
            return True
        title = published.get("title")
        if message.legacy_title and isinstance(title, str) and message.legacy_title in title:
            return True
    return False


def receipt_path(canonical: Path, message_id: str) -> Path:
    return canonical / RECEIPTS_DIR / f"{message_id}.json"


def _staged_path(canonical: Path, message_id: str) -> Path:
    return canonical / STAGED_DIR / f"{message_id}.json"


def _next_seq(canonical: Path) -> int:
    return 1 + max((seq for seq, _ in _outbox_files(canonical).values()), default=0)


def _write_receipt(canonical: Path, message: dict, outbox_file: Path) -> None:
    receipt = {
        "id": message["id"],
        "seq": message["seq"],
        "file": outbox_file.relative_to(canonical).as_posix(),
        "published_rev": message.get("published_rev"),
        "from": message.get("from"),
        "published_at": now_iso(),
    }
    write_json(receipt_path(canonical, message["id"]), receipt)


def _published_form(message: dict, seq: int, rev: str) -> dict:
    """``message`` as published: numbered, at ``rev``, every ``@result`` ref rev replaced."""
    refs = [
        ref | {"rev": rev} if ref.get("rev") in (RESULT_REV, None) else ref
        for ref in (ref_dict(ref) for ref in message["refs"])
    ]
    return message | {"refs": refs, "seq": seq, "published_rev": rev}


def _write_outbox(canonical: Path, message: dict) -> None:
    """Write the outbox file, then the receipt, then remove the staged copy (if any)."""
    check_message(message, outbox=True)
    outbox_file = canonical / OUTBOX_DIR / outbox_name(message["seq"], message["id"])
    write_json(outbox_file, message)
    _checkpoint("outbox_written")
    _write_receipt(canonical, message, outbox_file)
    _checkpoint("receipt_written")
    _staged_path(canonical, message["id"]).unlink(missing_ok=True)
    _checkpoint("staged_removed")


# --- git ---


def _changed_paths(canonical: Path, directory: str) -> list[str]:
    """Paths under ``directory`` that differ from HEAD (new, changed or deleted)."""
    output = gitops.git(
        canonical, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", directory
    )
    entries = [entry for entry in output.split("\0") if entry]
    paths, skip = [], False
    for entry in entries:
        if skip:  # the source path of a rename/copy entry
            skip = False
            continue
        paths.append(entry[3:])
        skip = entry[0] in "RC"
    return paths


def _staged_at_head(canonical: Path) -> list[str]:
    """Staged message ids committed at HEAD, oldest id first. Only these are published: a
    staged message reaches canonical by a merge, never by an uncommitted write."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{STAGED_DIR}/")
    names = [path.rsplit("/", 1)[-1] for path in listing.splitlines()]
    return sorted(name.removesuffix(".json") for name in names if name.endswith(".json"))


def _commit_pending(canonical: Path, message: str) -> str | None:
    """Commit the outbox files, receipts and staged-file removals that differ from HEAD."""
    staged_removed = [
        f"{STAGED_DIR}/{message_id}.json"
        for message_id in _staged_at_head(canonical)
        if not _staged_path(canonical, message_id).exists()
    ]
    paths = [*_changed_paths(canonical, OUTBOX_DIR), *_changed_paths(canonical, RECEIPTS_DIR)]
    paths += staged_removed
    if not paths:
        return None
    return provenance.commit_recorded(
        canonical, message, lambda: gitops.prepare_paths(canonical, sorted(set(paths)), message)
    )


def push(canonical: Path, allow_no_remote: bool = False) -> bool:
    """Push ``main`` to ``origin``; True if it got there. Without an ``origin`` remote,
    raise PublishError, or return False when ``allow_no_remote``."""
    if REMOTE not in gitops.git(canonical, "remote").split():
        if allow_no_remote:
            return False
        raise PublishError(f"canonical {canonical} has no {REMOTE!r} remote to push to")
    return provenance.push_main(canonical, REMOTE)


def commit_and_push(canonical: Path, paths: list[Path], message: str) -> bool:
    """Commit exactly ``paths`` (absolute, under canonical) and push; for the other comms
    writes (inbox, seen ledger). A missing remote is tolerated here: the commit is durable
    and queue-keeper pushes ``main`` every run. Returns whether the push happened. Call under
    the canonical lock."""
    relative = [Path(path).relative_to(canonical).as_posix() for path in paths]
    provenance.commit_recorded(
        canonical, message, lambda: gitops.prepare_paths(canonical, relative, message)
    )
    return push(canonical, allow_no_remote=True)


# --- recovery and publication ---


def _rebuild_outbox_file(canonical: Path, message_id: str, receipt: dict) -> None:
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    staged = _staged_path(canonical, message_id)
    if content is None and staged.exists():
        content = staged.read_bytes()
    if content is None:
        raise PublishError(
            f"receipt {receipt_path(canonical, message_id)} has no outbox file and no staged "
            "message to rebuild it from"
        )
    message = _published_form(
        _as_agent(json.loads(content)), receipt["seq"], receipt["published_rev"]
    )
    write_json(canonical / OUTBOX_DIR / outbox_name(receipt["seq"], message_id), message)


def _receipts(canonical: Path) -> dict[str, dict]:
    return {path.stem: read_json(path) for path in (canonical / RECEIPTS_DIR).glob("M-*.json")}


def recover(canonical: Path) -> None:
    """Complete any publication a crash interrupted, then commit the leftovers (C5)."""
    outbox = _outbox_files(canonical)
    receipts = _receipts(canonical)
    for message_id, (seq, path) in outbox.items():
        if message_id not in receipts:
            message = read_json(path)
            message = message if isinstance(message, dict) else {}
            _write_receipt(canonical, message | {"id": message_id, "seq": seq}, path)
            receipts[message_id] = read_json(receipt_path(canonical, message_id))
    for message_id, receipt in receipts.items():
        if message_id not in outbox:
            _rebuild_outbox_file(canonical, message_id, receipt)
    for message_id in receipts:
        _staged_path(canonical, message_id).unlink(missing_ok=True)
    _commit_pending(canonical, "publish: recover interrupted publication")


def _as_agent(message: dict) -> dict:
    """A staged message is always from the research agent: a worker cannot publish as the
    runtime, whatever its file says."""
    return {**message, "from": FROM_AGENT}


def _added_by(canonical: Path, message_id: str) -> str | None:
    """The first-parent commit of canonical that added this staged file (its card's merge)."""
    sha = gitops.git(
        canonical,
        "log",
        "--first-parent",
        "--diff-filter=A",
        "-1",
        "--format=%H",
        "--",
        f"{STAGED_DIR}/{message_id}.json",
    )
    return sha or None


def _staged_message(
    canonical: Path, message_id: str, seq: int, fallback_rev: str, card: str | None = None
) -> dict:
    """The staged message in published form; raises MessageProblems if it is invalid. When
    ``card`` is given and the message arrived with ``fallback_rev`` (that card's merge), the
    message records ``card``: the runtime sets it, whatever the worker wrote."""
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    try:
        staged = json.loads(content)
    except ValueError as error:
        raise MessageProblems([f"not valid JSON: {error}"]) from None
    if not isinstance(staged, dict):
        raise MessageProblems(["a message must be a JSON object"])
    rev = _added_by(canonical, message_id) or fallback_rev
    if card is not None and rev == fallback_rev:
        staged = staged | {"card": card}
    message = _published_form(_as_agent(staged), seq, rev)
    check_message(message, outbox=True)
    return message


def publish_staged(
    canonical: Path, merge_sha: str, allow_no_remote: bool = False, card: str | None = None
) -> PublishResult:
    """Publish every staged message committed in canonical (they arrive by merges). Each
    message's ``@result`` becomes the commit that added it (``merge_sha`` only if git finds
    none). With ``card`` (the publishing handoff's task id), the messages that ``merge_sha``
    added get a ``card`` field, so a later reply finds its thread's card. Invalid messages
    are skipped and reported. Idempotent: a rerun publishes nothing twice and pushes again.
    Raises PublishError (no remote, unrepairable state)."""
    recover(canonical)
    receipts = _receipts(canonical)
    result = PublishResult()
    next_seq = _next_seq(canonical)
    for message_id in _staged_at_head(canonical):
        if message_id in receipts:  # recover already removed it
            continue
        try:
            message = _staged_message(canonical, message_id, next_seq, merge_sha, card)
        except MessageProblems as error:
            result.skipped[message_id] = error.problems
            continue
        _write_outbox(canonical, message)
        result.seqs.append(next_seq)
        next_seq += 1
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish {len(result.seqs)} message(s) after {merge_sha[:12]}")
    _checkpoint("before_push")
    result.pushed = push(canonical, allow_no_remote)
    return result


def publish_runtime(
    canonical: Path,
    kind: str,
    title: str,
    body: str,
    refs: list,
    allow_no_remote: bool = False,
    **fields,
) -> PublishResult:
    """Publish one runtime-originated message (``from: runtime``) at canonical HEAD; refs
    with rev ``@result`` (the default) are read at HEAD. ``result.seq`` is its number.
    Raises MessageProblems (nothing written) or PublishError."""
    recover(canonical)
    head = gitops.head(canonical)
    message = build_message(kind, title, body, refs, FROM_RUNTIME, **fields)
    seq = _next_seq(canonical)
    message = _published_form(message, seq, head)
    check_message(message, outbox=True)
    _write_outbox(canonical, message)
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish runtime {kind} {seq}")
    _checkpoint("before_push")
    return PublishResult(seqs=[seq], pushed=push(canonical, allow_no_remote))


def publish_message(
    canonical: Path, message: RuntimeMessage, allow_no_remote: bool = False, **fields
) -> PublishResult:
    """Publish one runtime message built by ``runtime_messages`` (the only way runtime code
    publishes): its refs, plus its ``about``, ``runtime_key`` and kind-derived fields.
    ``fields`` adds or overrides envelope fields such as ``priority``, ``expects_response`` or
    ``in_reply_to``."""
    return publish_runtime(
        canonical,
        message.kind,
        message.title,
        message.body,
        message.refs,
        allow_no_remote,
        **{**message.fields(), **fields},
    )


# --- inbound acknowledgement (C11) ---


def find_reply(
    canonical: Path, target: str, sender: str | None = None, card: str | None = None
) -> str | None:
    """Id of a message in the outbox (on disk) with ``in_reply_to`` == ``target``, from
    ``sender`` and published by card ``card`` if given; the oldest one if several."""
    for message_id, (_, path) in sorted(
        _outbox_files(canonical).items(), key=lambda item: item[1][0]
    ):
        message = read_json(path)
        if not isinstance(message, dict) or message.get("in_reply_to") != target:
            continue
        if sender is not None and message.get("from") != sender:
            continue
        if card is None or message.get("card") == card:
            return message_id
    return None


def _message_with_key(canonical: Path, key: str) -> str | None:
    """Id of the outbox message (on disk) whose ``runtime_key`` is ``key``, if any."""
    for message_id, (_, path) in _outbox_files(canonical).items():
        message = read_json(path)
        if isinstance(message, dict) and message.get("runtime_key") == key:
            return message_id
    return None


def message_id_of_seq(canonical: Path, seq: int) -> str:
    (message_id,) = [mid for mid, (number, _) in _outbox_files(canonical).items() if number == seq]
    return message_id


def _is_responsible(record: dict, handoff: dict) -> bool:
    responsible = record["responsible"]
    return (
        responsible["task_id"] == handoff["card"]
        or responsible["request_id"] == handoff["request_id"]
        or record["id"] in handoff.get("responsible_inbound", [])
    )


def _clip(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    line = " ".join(text.split())
    if len(line) > SUMMARY_LIMIT:
        line = line[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"
    return line or None


def _journal(canonical: Path, handoff: dict) -> tuple[str | None, str | None]:
    """(journal id, its opening paragraph) of the card's journal at its merge. The journal's
    first paragraph is "what you did, what you found, and what is open" (complete-card)."""
    path = handoff.get("journal")
    content = gitops.show(canonical, handoff["merge_sha"], path) if path else None
    if content is None:
        return None, None
    doc = frontmatter.loads(content.decode("utf-8", errors="replace"))
    lead = re.split(r"^#{1,6} ", doc.content, maxsplit=1, flags=re.MULTILINE)[0]
    paragraph = next((part for part in re.split(r"\n\s*\n", lead) if part.strip()), "")
    journal_id = doc.metadata.get("id")
    return (str(journal_id) if journal_id else None), _clip(paragraph)


def _sent(canonical: Path, card: str) -> list[dict]:
    """The messages this card published, in outbox order."""
    sent = []
    for _seq, path in sorted(_outbox_files(canonical).values()):
        message = read_json(path)
        if not isinstance(message, dict) or message.get("card") != card:
            continue
        if message.get("from") == FROM_AGENT and isinstance(message.get("title"), str):
            sent.append(message)
    return sent


def _ack_refs(handoff: dict, journal_id: str | None) -> list[dict]:
    """The handoff record (read at the publishing HEAD), then the journal and the records the
    card created, read at its merge."""
    refs = [{"uri": f"ar://handoff/{handoff['card']}/{handoff['run_id']}", "role": "subject"}]
    record_ids = [journal_id] if journal_id else []
    record_ids += [rid for rid in handoff["report"]["created"] if rid not in record_ids]
    for record_id in record_ids[:REFERENCED_RECORDS]:
        try:
            entity = entity_of_id(record_id).name
        except ValueError:
            continue
        refs.append(
            {"uri": f"ar://{entity}/{record_id}", "rev": handoff["merge_sha"], "role": "evidence"}
        )
    return refs


def _task_title(canonical: Path, inbound_id: str) -> str | None:
    content = gitops.show(canonical, "HEAD", f"{INBOX_DIR}/{inbound_id}.json")
    message = json.loads(content) if content is not None else None
    title = message.get("title") if isinstance(message, dict) else None
    return title if isinstance(title, str) else None


def _task_acknowledgement(
    canonical: Path,
    inbound_id: str,
    handoff: dict,
    backlog: bool,
    allow_no_remote: bool,
    follow_up_of: str | None = None,
) -> str:
    """The runtime note acknowledging an inbound ``task`` (published once: a rerun after a
    crash finds the note already in the outbox). Its title is the task as the sender phrased
    it plus the outcome; its body is the card's own completion summary, then what follows from
    the outcome and the titles of the messages the card sent. The summary is the one the run
    ended with on the board (``run_summary``, from ``process_handoffs``) or the opening of the
    card's journal, whichever names no ids (``runtime_messages.plainest_summary``). For a
    blocked card the journal comes first: its board summary is the ``kanban_block`` reason,
    which its help request already carries. Ids and commits go into ``refs`` and ``about``.

    ``follow_up_of``: the id of the ``blocked`` note this follow-up completes. The follow-up has
    ``about.follows`` = that id and its own ``runtime_key``, by which a rerun finds it
    (``find_reply`` would find the ``blocked`` note)."""
    key = None
    if follow_up_of is None:
        found = find_reply(canonical, inbound_id, FROM_RUNTIME)
    else:
        key = runtime_messages.final_acknowledgement_key(inbound_id)
        found = _message_with_key(canonical, key)
    if found is not None:
        return found
    journal_id, journal_summary = _journal(canonical, handoff)
    outcome = str(handoff.get("outcome"))
    sent = _sent(canonical, handoff["card"])
    summaries = [_clip(handoff.get("run_summary")), journal_summary]
    if outcome == "blocked":
        summaries.reverse()
    message = runtime_messages.task_acknowledgement(
        task_title=_task_title(canonical, inbound_id),
        outcome=outcome,
        summary=runtime_messages.plainest_summary(summaries),
        sent_titles=[message["title"] for message in sent],
        backlog=backlog,
        has_journal=journal_id is not None,
        asked_for_help=any(message.get("kind") == "help_request" for message in sent),
        refs=_ack_refs(handoff, journal_id),
        about={
            "card": handoff["card"],
            "request": handoff["request_id"],
            "merge_commit": handoff["merge_sha"],
            "outcome": outcome,
            **({"follows": follow_up_of} if follow_up_of else {}),
        },
        key=key,
    )
    result = publish_message(
        canonical, message, allow_no_remote=allow_no_remote, in_reply_to=inbound_id
    )
    return message_id_of_seq(canonical, result.seq)


def _blocked_note(outbox: dict[str, tuple[int, Path]], record: dict) -> dict | None:
    """The note that acknowledged ``record`` (a ``task``) with outcome ``blocked``, if no
    follow-up has been recorded for it yet. ``outbox`` is ``_outbox_files``."""
    if record["state"] != "acknowledged" or record["kind"] != "task" or record.get("final_ack_by"):
        return None
    found = outbox.get(record["acknowledged_by"])
    note = read_json(found[1]) if found else None
    about = note.get("about") if isinstance(note, dict) else None
    blocked = isinstance(about, dict) and about.get("outcome") == "blocked"
    return note if blocked else None


def _repair_requests(canonical: Path, blocked_card: str) -> list[str]:
    """The repair requests written for ``blocked_card``, then for the cards of those requests,
    up to ``REPAIR_HOPS`` deep. Followed through runtime-written records only: the card's
    blocked marker names its ``repair_request`` (``runtime.blocked``), that request must be
    created by the runtime, and its receipt's ``tasks`` are the next cards."""
    found, cards = [], [blocked_card]
    for _hop in range(REPAIR_HOPS):
        next_cards = []
        for card in cards:
            marker = committed_json(canonical, blocked_marker_path(canonical, card))
            request_id = marker.get("repair_request") if isinstance(marker, dict) else None
            if not request_id or not isinstance(request_id, str):
                continue
            request = committed_json(
                canonical, Layout(canonical).card_requests / f"{request_id}.json"
            )
            spec = request.get("spec") if isinstance(request, dict) else None
            if not isinstance(spec, dict) or spec.get("created_by") != CREATED_BY_RUNTIME:
                continue
            found.append(request_id)
            receipt = committed_json(
                canonical, request_receipt_path(ledger_of(canonical), request_id)
            )
            next_cards += [task["task_id"] for task in (receipt or {}).get("tasks", [])]
        cards = next_cards
    return found


def _finishes_blocked_work(canonical: Path, record: dict, blocked_card: str, handoff: dict) -> bool:
    """The handoff is of the card responsible for ``record``, or of a repair card of
    ``blocked_card`` (the ``blocked`` note's ``about.card``), or of a repair of a repair
    (``_repair_requests``). The same card hands off again only when health requeued it before
    its ``blocked`` handoff was published: a published handoff closes the request, and
    ``card finish`` refuses a closed request (``runtime.blocked`` then repairs the card)."""
    if _is_responsible(record, handoff):
        return True
    return handoff["request_id"] in _repair_requests(canonical, blocked_card)


def _final_acknowledgements(canonical: Path, handoff: dict, allow_no_remote: bool) -> list[Path]:
    """Publish the follow-up of each ``task`` acknowledged ``blocked`` whose work this handoff
    finishes with a final outcome, and record it as ``final_ack_by``; return the records
    written (the caller commits them). The note is committed by its own publication, before
    the record: a rerun after a crash between the two finds the note by its ``runtime_key``
    and only writes the record."""
    outcome = handoff.get("outcome")
    if outcome not in FINAL_OUTCOMES:
        return []
    written = []
    outbox = _outbox_files(canonical)
    for inbound_id, record in delivery.records(canonical).items():
        first = _blocked_note(outbox, record)
        if first is None:
            continue
        blocked_card = first["about"]["card"]  # set with outcome (_task_acknowledgement)
        if not _finishes_blocked_work(canonical, record, blocked_card, handoff):
            continue
        # The planning-card lead line is only true when the card that took it finishes it.
        backlog = record["responsible"]["backlog"] and blocked_card == handoff["card"]
        message_id = _task_acknowledgement(
            canonical, inbound_id, handoff, backlog, allow_no_remote, follow_up_of=first["id"]
        )
        _checkpoint("final_ack_published")
        final = delivery.moved(
            record, "acknowledged", note=f"final outcome {outcome}", final_ack_by=message_id
        )
        written.append(delivery.write_record(canonical, final))
    return written


def acknowledge_inbound(canonical: Path, handoff: dict, allow_no_remote: bool = False) -> None:
    """After a handoff's messages are published: move each routed inbound this card is
    responsible for to ``acknowledged`` (C11), as its kind's ``x-autoresearch.ack`` says
    (``comms.kinds``). ``card`` (``reply``, ``steer``): the card's own published ``note`` with
    ``in_reply_to`` (its ``card`` must be this handoff's card; validation required the note).
    ``runtime`` (``task``): a runtime note published here. ``none``: left as it is. One the
    card did not answer stays ``routed``; routing
    returns it to the planning backlog once the card's request is closed.

    Then a ``task`` already acknowledged ``blocked`` gets one follow-up note (``about.outcome``
    the final outcome, ``about.follows`` the ``blocked`` note, ``runtime_key``
    ``ack-final:<inbound id>``, recorded as ``final_ack_by``) when this handoff finishes its
    work with ``done``, ``partial``, ``dropped`` or ``failed`` (``_final_acknowledgements``).
    The IDE bridge closes the task it showed as paused with it. Under the lock; idempotent."""
    changed = []
    for inbound_id, record in delivery.records(canonical).items():
        if record["state"] != "routed" or not _is_responsible(record, handoff):
            continue
        ack = kinds.get("inbound", record["kind"]).ack
        if ack == "card":
            # Only the responsible card's own note counts: a card that showed the message
            # read-only and answered it anyway does not acknowledge it.
            message_id = find_reply(canonical, inbound_id, FROM_AGENT, card=handoff["card"])
        elif ack == "runtime":
            backlog = record["responsible"]["backlog"]
            message_id = _task_acknowledgement(
                canonical, inbound_id, handoff, backlog, allow_no_remote
            )
            _checkpoint("ack_published")
        elif ack == "none":
            continue
        else:
            raise ValueError(f"inbound kind {record['kind']!r} has unknown ack {ack!r}")
        if message_id is None:
            continue
        acknowledged = delivery.moved(record, "acknowledged", acknowledged_by=message_id)
        changed.append(delivery.write_record(canonical, acknowledged))
    changed += _final_acknowledgements(canonical, handoff, allow_no_remote)
    if changed:
        commit_and_push(canonical, changed, f"inbound acknowledged by {handoff['card']}")


def publish_hook(
    canonical: Path, merge_sha: str, handoff: dict, allow_no_remote: bool = False
) -> PublishResult:
    """The ``publish`` hook for ``runtime.merge.process_handoffs``: called under the canonical
    lock after a merge; idempotent. Publishes the staged messages (those the card's merge
    added carry ``card``: the handoff's task id), then acknowledges the inbound the card is
    responsible for. ``process_handoffs`` ignores the return value, so a caller that needs
    ``pushed``/``skipped`` wraps this hook (queue-keeper, Task 3.9). Tests bind
    ``allow_no_remote`` with ``functools.partial``."""
    result = publish_staged(canonical, merge_sha, allow_no_remote, card=handoff["card"])
    acknowledge_inbound(canonical, handoff, allow_no_remote)
    return result

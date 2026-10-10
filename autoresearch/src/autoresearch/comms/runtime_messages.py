"""The text of every message the runtime publishes (``from: runtime``).

Runtime messages follow ``doctrine/communication.md`` like the agent's own: the title says in
plain words what happened, and the body says what it means and what, if anything, the reader
must do. They are written for the Preston-role agent, but humans read them too: the Softmax
IDE bridge pastes a task acknowledgement into the IDE task as its outcome text.

So ids (inbound, request, card and message ids) and commits never go into a title or body.
They go into ``refs`` (``ar://`` references with a ``rev``) or into two additive envelope
fields (``schemas/comms/envelope.outbound.json``):

- ``about``: name -> id of what the message is about (``card``, ``request``,
  ``merge_commit``, ...), for a reader that needs to look further. A note answering an inbound
  also has ``outcome``: the card outcome (``done``, ``partial``, ...), or ``rejected``.
- ``runtime_key``: a stable key the runtime uses to publish a message only once
  (``publisher.already_published``). Messages published before ``runtime_key`` existed
  (2026-10-08) carried their key in the title instead; a builder sets ``legacy_title`` to that
  old title, so a message published before the upgrade still counts and is not repeated.

A ``help_request`` also carries ``priority`` P0 and ``expects_response`` (``HELP_REQUEST_FIELDS``),
so every publisher of the same message sends the same envelope.

The card's own words (its summary, the titles of its messages) go into a task
acknowledgement too. They should be plain already (complete-card asks for that), but when they
name ids anyway, ``plain_ids`` replaces each id with what it names ("the hypothesis").

Each builder here returns a ``RuntimeMessage``; ``publisher.publish_message`` publishes it.
``tests/contract/comms/test_runtime_messages.py`` publishes every builder and checks that no
id or commit reaches the text, and that runtime code publishes nothing else.
"""

import hashlib
import re
from dataclasses import dataclass, field

from autoresearch.ids import ENTITY_PREFIXES

TITLE_LIMIT = 120
# A help_request is always urgent and wants an answer, whoever publishes it: the first
# publisher of a ``runtime_key`` wins, so these must not depend on the call site.
HELP_REQUEST_FIELDS = {"priority": "P0", "expects_response": True}
OUTCOME_WORDS = {
    "done": "Done",
    "partial": "Partly done",
    "dropped": "Dropped",
    "failed": "Failed",
    "blocked": "Blocked",
}
# What the agent did, when its card left no summary to quote.
_NO_SUMMARY = {
    "done": "The agent finished this task but left no summary.",
    "partial": "The agent did part of this task but left no summary.",
    "dropped": "The agent dropped this task and left no summary.",
    "failed": "The agent could not do this task and left no summary.",
    "blocked": "The agent stopped work on this task and left no summary.",
}
# What follows from the outcome, for the reader. ``partial`` creates no follow-up card in the
# runtime: what is left may wait on credits or the XP poller, or need a new task.
_NEXT = {
    "partial": "The rest of the task is not done yet.",
    "dropped": "No more work will be done on it. Send a new task if it still matters.",
    "failed": "No more work will be done on it. Send a new task if it still matters.",
}
_BLOCKED_ASKED = "The agent needs help to go on; its help request says what it needs."
_BLOCKED_SILENT = "The agent needs help to go on, but it sent no help request about it."
# ``about.outcome`` of a note that answers an inbound it did not act on, so a reader such as
# the IDE bridge can tell a rejection from a card outcome without parsing the title.
REJECTED = "rejected"
_SENT_LISTED = 3
_UNRESOLVED_REF = "unresolved ref "
_REBUILD_LOST = "board rebuild could not re-admit "

# Ids and commits as they appear in free text: an ar:// reference, a record or runtime id
# (``H-<ULID>``, ``Q-<ULID>``, ``K-<ULID>``, ...), a Kanban task id (``t_6c0a018c``), or a
# commit (7 to 40 hex characters with at least one digit and one letter).
ID_TOKEN = re.compile(
    r"ar://[^\s,;)\]]*[^\s,;)\].]"  # never ends on a sentence's full stop
    r"|\b[A-Z]{1,3}-[0-9A-HJKMNP-TV-Z]{26}\b"
    r"|\bt_[0-9a-f]{4,}\b"
    r"|\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
)
# The " (<content hash>)" a skill proposal's title ended with before runtime_key existed.
_LEGACY_HASH = re.compile(r" \([0-9a-f]{12}\)$")
_NAME_BY_PREFIX = {prefix: name for name, prefix in ENTITY_PREFIXES.items()}
_NAME_BY_PREFIX.update({"Q": "request", "I": "inbound message", "K": "card", "P": "promotion"})


@dataclass(frozen=True)
class RuntimeMessage:
    kind: str
    title: str
    body: str
    refs: list = field(default_factory=list)
    about: dict[str, str] = field(default_factory=dict)
    key: str | None = None  # the runtime_key: published once
    legacy_title: str | None = None  # its title before runtime_key existed (never published)

    def fields(self) -> dict:
        """The additive envelope fields of this message."""
        found: dict = {}
        if self.about:
            found["about"] = dict(self.about)
        if self.key is not None:
            found["runtime_key"] = self.key
        if self.kind == "help_request":
            found.update(HELP_REQUEST_FIELDS)
        return found


def _line(text: str, limit: int = TITLE_LIMIT) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _title(lead: str, subject: str | None) -> str:
    return _line(f"{lead}: {subject}" if subject and subject.strip() else lead)


def _about(**ids) -> dict[str, str]:
    return {name: str(value) for name, value in ids.items() if value not in (None, "", [])}


def _count(items: list, noun: str) -> str:
    """ "its recovery card" for one item, "all 3 recovery cards" for more."""
    return f"its {noun}" if len(items) == 1 else f"all {len(items)} {noun}s"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _quoted_list(titles: list[str]) -> str:
    shown = [f"“{_line(title, 80)}”" for title in titles[:_SENT_LISTED]]
    more = len(titles) - len(shown)
    return "; ".join(shown) + (f"; and {more} more" if more else "")


def _plain_id(match: re.Match) -> str:
    token = match.group(0)
    if token.startswith("ar://"):
        return f"the {token.removeprefix('ar://').split('/', 1)[0].split('#', 1)[0]}"
    if token.startswith("t_"):
        return "the card"
    prefix, dash, _ = token.partition("-")
    if dash:
        return f"the {_NAME_BY_PREFIX.get(prefix, 'record')}"
    return "a commit"


def has_ids(text: str) -> bool:
    return ID_TOKEN.search(text) is not None


def plain_ids(text: str) -> str:
    """``text`` with each id or commit replaced by what it names: ``ar://hypothesis/H-…`` and
    ``H-…`` become "the hypothesis", ``t_…`` "the card", a commit "a commit"."""
    return ID_TOKEN.sub(_plain_id, text)


def plainest_summary(candidates: list[str | None]) -> str | None:
    """The first candidate summary that names no ids; else the first one, ids made plain."""
    present = [text for text in candidates if text and text.strip()]
    for text in present:
        if not has_ids(text):
            return text
    return plain_ids(present[0]) if present else None


# --- inbound task: acknowledgement and rejection ---


def task_acknowledgement(
    task_title: str | None,
    outcome: str,
    summary: str | None,
    sent_titles: list[str],
    backlog: bool,
    has_journal: bool,
    asked_for_help: bool,
    refs: list,
    about: dict[str, str],
) -> RuntimeMessage:
    """The note that closes an inbound ``task`` once its card is published (C11). ``summary``
    is the card's own completion summary, if it left one (``plainest_summary``);
    ``sent_titles`` the titles of the messages the card published; ``asked_for_help``
    whether one of them is a ``help_request``."""
    parts = []
    if backlog:
        parts.append("A planning card that was already open took this task on with its other work.")
    if summary:
        parts.append(summary.strip())
    else:
        parts.append(_NO_SUMMARY.get(outcome, "The agent finished work on this task."))
        if has_journal:
            parts.append("Its journal entry has the details.")
    if outcome in _NEXT:
        parts.append(_NEXT[outcome])
    if outcome == "blocked":
        parts.append(_BLOCKED_ASKED if asked_for_help else _BLOCKED_SILENT)
    if sent_titles:
        titles = [plain_ids(title) for title in sent_titles]
        parts.append(f"The agent also sent: {_quoted_list(titles)}.")
    lead = OUTCOME_WORDS.get(outcome, str(outcome).capitalize())
    title = _title(lead, task_title or "your task")
    return RuntimeMessage("note", title, " ".join(parts), refs, about)


def task_not_started(task_title: str | None, problems: list[str]) -> RuntimeMessage:
    """A ``task`` that cannot become a card: the message itself has problems."""
    body = (
        "This task was not started, because the message has problems:\n\n"
        f"{_bullets(problems)}\n\nSend it again as a new message with these fixed."
    )
    title = _title("Not started", task_title or "your task")
    return RuntimeMessage("note", title, body, about=_about(outcome=REJECTED))


def _plain_admission_reason(reason: str) -> tuple[str, str]:
    """(why, what to send instead) for an admission rejection reason."""
    parts = [part.strip() for part in reason.split("; ") if part.strip()]
    missing = [
        part.removeprefix(_UNRESOLVED_REF).split(": ", 1)[0]
        for part in parts
        if part.startswith(_UNRESOLVED_REF)
    ]
    if missing:
        return (
            "it names references that do not point to anything in the workspace: "
            + ", ".join(missing),
            "Send it again as a new message with references that exist.",
        )
    if any(part.startswith(_REBUILD_LOST) for part in parts):
        return (
            "the work board was lost, and its card could not be made again",
            "Send it again as a new message if it still matters.",
        )
    return (
        "the runtime could not start a card for it",
        "Send it again as a new message if it still matters.",
    )


def task_not_admitted(task_title: str | None, reason: str) -> RuntimeMessage:
    """A ``task`` whose card request admission rejected (not for planning ownership)."""
    why, instead = _plain_admission_reason(reason)
    body = f"This task was not started: {why}. {instead}"
    title = _title("Not started", task_title or "your task")
    return RuntimeMessage("note", title, body, about=_about(outcome=REJECTED, reason=reason))


def inbox_write_rejected(problems: list[str]) -> RuntimeMessage:
    """A file written directly into ``comms/inbox/`` that is not a valid message."""
    body = (
        "A message written to comms/inbox/ was not delivered, because:\n\n"
        f"{_bullets(problems)}\n\nFix these and write it again under a new id. This id is "
        "closed: the runtime ignores a rewrite under the same id."
    )
    title = "Not delivered: the message is not valid"
    return RuntimeMessage("note", title, body, about=_about(outcome=REJECTED))


# --- health and blocked cards ---


def health_problem(kind: str, problem: str, day: str) -> RuntimeMessage:
    """One host problem the health job found; published once per kind per UTC day."""
    body = (
        f"The health check found a problem on the host: {problem}. Research cards may fail "
        "until it is fixed. Please fix it on the host."
    )
    return RuntimeMessage(
        "help_request",
        f"Health check found a problem: {kind}",
        body,
        key=f"health:{kind}:{day}",
        legacy_title=f"Health: {kind} problem ({day})",
    )


def card_blocked_twice(
    card_title: str, task_id: str, request_id: str, repair_request: str | None, refs: list
) -> RuntimeMessage:
    """Hermes blocked a card again after the runtime requeued it once."""
    follow_up = (
        "A repair card will redo its work."
        if repair_request
        else "The recovery card of its promotion carries its work on."
    )
    body = (
        f"Hermes blocked the card “{_line(card_title, 80)}” twice, the second time after the "
        "runtime put it back in the queue. That usually means an operational problem, such as "
        f"bad credentials or a tripped circuit breaker. {follow_up} Please check the Hermes "
        "logs for the cause and fix it."
    )
    return RuntimeMessage(
        "help_request",
        _title("Blocked twice", card_title),
        body,
        refs,
        _about(card=task_id, request=request_id, repair_request=repair_request),
        key=f"blocked-twice:{task_id}",
        legacy_title=f"Card {task_id} is blocked again after a requeue",
    )


def card_tool_fault(
    card_title: str, reason: str, task_id: str, request_id: str, run_id: str, refs: list
) -> RuntimeMessage:
    """The agent blocked a card on a tool fault (D-26): no requeue, no repair card."""
    body = (
        f"The agent stopped the card “{_line(card_title, 80)}” because one of its tools "
        f"failed: {reason}. A retry will not fix this, so the runtime has not requeued the "
        "card or opened a repair card. Please fix the tool, then unblock the card."
    )
    return RuntimeMessage(
        "help_request",
        _title("Blocked by a tool fault", card_title),
        body,
        refs,
        _about(card=task_id, request=request_id, run=run_id),
        key=f"tool-fault:{task_id}:{run_id}",
        legacy_title=f"Card {task_id} is blocked by a tool fault (run {run_id})",
    )


def foreign_card(
    card_title: str | None, task_id: str, board: str, forged_handoff: bool = False
) -> RuntimeMessage:
    """A board card the runtime did not create (no request behind it); the runtime skips it
    for good (``runtime.foreign``). ``forged_handoff``: its worker also wrote a receipt and a
    handoff into the canonical workspace itself."""
    title = plain_ids(card_title) if card_title and card_title.strip() else None
    named = f"the card “{_line(title, 80)}”" if title else "a card with no title"
    forged = (
        " Its worker also wrote runtime records (a request receipt and a handoff) into the "
        "canonical workspace itself, which only the runtime may do: please tell the operator."
        if forged_handoff
        else ""
    )
    body = (
        f"The board has {named}, but the runtime did not create it: it has no request behind "
        "it, so it skipped admission. Something added it to the board directly, for example an "
        "agent creating a card itself. The runtime leaves it alone: none of its work is merged "
        f"(what it finished stays on its card branch, unused), and it is not tracked.{forged} "
        f"To remove it, archive it with `hermes kanban --board {board} archive <card>`, where "
        "<card> is the card named in this message's about field. If its work should still be "
        "done, send it as a new task, so the agent does it through a request the runtime admits."
    )
    return RuntimeMessage(
        "help_request",
        _title("Card not created by the runtime", title),
        body,
        [],
        _about(card=task_id),
        key=f"foreign-card:{task_id}",
    )


def queue_op_failed(
    op: str,
    card_title: str | None,
    tries: int,
    error: str,
    request_id: str,
    task_id: str | None,
    board: str,
) -> RuntimeMessage:
    """A reprioritize or drop request failed several runs in a row; the runtime gave up on it
    (``runtime.queue_ops``). The other requests and steps went on."""
    title = plain_ids(card_title) if card_title and card_title.strip() else None
    named = f"the card “{_line(title, 80)}”" if title else "a card"
    verb = "drop" if op == "drop" else "change the priority of"
    if op == "drop":
        what_now = (
            "The card stays on the board and may still run. To remove it, archive it with "
            f"`hermes kanban --board {board} archive <card>`, where <card> is the card named "
            "in this message's about field; the runtime then records the drop."
        )
    else:
        what_now = "The card keeps its old priority. Send a new task if the order matters."
    body = (
        f"The agent asked to {verb} {named}, but the runtime could not, in {tries} tries, "
        f"and has stopped trying. Last error: {plain_ids(error)}. {what_now}"
    )
    return RuntimeMessage(
        "help_request",
        _title(f"Could not {verb} a card", title),
        body,
        [],
        _about(request=request_id, card=task_id),
        key=f"queue-op-failed:{request_id}",
    )


# --- spend caps (runtime.spend) ---


# What happened to a card at its spend cap (``card_over_cap``).
STOPPED = "stopped"  # blocked by the runtime
TRIAGED = "triaged"  # blocked, but Hermes moved it to triage
HELD = "held"  # already blocked; kept blocked instead of being requeued
NOT_STOPPED = "not_stopped"  # the block failed
_CAP_LEAD = {
    STOPPED: "The runtime stopped the card “{title}” and blocked it, because {why}.",
    TRIAGED: (
        "The runtime stopped the card “{title}”, because {why}. Hermes put it in triage, not "
        "in blocked, because it had been blocked and unblocked before."
    ),
    HELD: "The runtime kept the blocked card “{title}” blocked, because {why}.",
    NOT_STOPPED: (
        "The runtime tried to stop the card “{title}”, because {why}. The card may still be "
        "running: please block it by hand with hermes kanban."
    ),
}
_CAP_NEXT = {
    STOPPED: "If the work should go on, unblock the card: it then gets {allowance}.",
    TRIAGED: (
        "Hermes has no command that moves a triage card back to the queue, so if the work "
        "should go on, drop the card and send the task again."
    ),
    HELD: "If the work should go on, unblock the card: it then gets {allowance}.",
    NOT_STOPPED: (
        "The runtime keeps trying to stop it every minute and will report again only after "
        "a stop works."
    ),
}


def card_over_cap(
    card_title: str,
    why: str,
    allowance: str,
    task_id: str,
    request_id: str,
    stop: int,
    refs: list,
    outcome: str = STOPPED,
) -> RuntimeMessage:
    """A card reached its run or estimated-cost cap. ``stop`` counts the card's recorded
    stops, so a card a human unblocked that reaches the cap again is reported again. For
    ``NOT_STOPPED`` it counts the stops that worked, so one failure streak (the runtime
    retries every minute) gets one message. A foreign card's title is worker-written, so ids
    in it are made plain."""
    card_title = plain_ids(card_title)
    title = _line(card_title, 80)
    body = (
        f"{_CAP_LEAD[outcome].format(title=title, why=why)} It will not be put back in the "
        f"queue or get a repair card. {_CAP_NEXT[outcome].format(allowance=allowance)} "
        "Otherwise leave it, or send a smaller task."
    )
    lead = "Card not stopped at its spend cap" if outcome == NOT_STOPPED else (
        "Card stopped at its spend cap"
    )
    return RuntimeMessage(
        "help_request",
        _title(lead, card_title),
        body,
        refs,
        _about(card=task_id, request=request_id),
        key=f"spend-cap:{task_id}:{stop}" + (":not-stopped" if outcome == NOT_STOPPED else ""),
    )


def daily_budget_reached(spent_usd: float, budget_usd: float, day: str) -> RuntimeMessage:
    """Today's estimated model spend reached the daily budget; queue-keeper paused (one per
    UTC day)."""
    body = (
        f"The agent is paused: today's estimated model spend (UTC day {day}) is "
        f"${spent_usd:.2f}, at or over the daily budget of ${budget_usd:.2f}. No new cards "
        "start; cards already running finish under their own caps. The agent resumes by "
        "itself on the next UTC day. To resume today, raise AUTORESEARCH_DAILY_BUDGET_USD in "
        "the profile .env (restart the gateway if the next queue-keeper run does not pick it "
        "up)."
    )
    return RuntimeMessage(
        "help_request",
        "Agent paused: daily spend budget reached",
        body,
        key=f"daily-budget:{day}",
    )


def card_models_invalid(value: str, problems: list[str]) -> RuntimeMessage:
    """Entries of AUTORESEARCH_CARD_MODELS were skipped (one per distinct value)."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    body = (
        "Some entries of AUTORESEARCH_CARD_MODELS in the profile .env cannot be used: "
        f"{'; '.join(problems)}. Cards of those modes run on the default model, which costs "
        "more. Please fix the value; modes are investigation, scouting, experimentation, "
        "maintenance and planning."
    )
    return RuntimeMessage(
        "help_request",
        "Card model setting has errors",
        body,
        key=f"card-models:{digest}",
    )


def host_requirements_missing(game: str, problems: list[str]) -> RuntimeMessage:
    """The workers lack something the game's ``host_requirements`` declare (one per game and
    distinct set of problems)."""
    digest = hashlib.sha256("\n".join(sorted(problems)).encode("utf-8")).hexdigest()[:12]
    body = (
        f"The {game} game needs these on the host for its builds, and the agent's workers do "
        f"not have them: {'; '.join(problems)}. Cards that build a policy will fail until "
        "they are set up; other work goes on. Please have the operator fix the host setup "
        "(the profile .env, or the provisioning that writes it). The game's GAME.md says "
        "what each one is."
    )
    return RuntimeMessage(
        "help_request",
        _title("Host setup missing for game builds", game),
        body,
        key=f"host-requirements:{game}:{digest}",
    )


# --- queue-keeper ---


def signal_persists(
    code: str, entity: str | None, detail: str, first_seen: str, iterations: int
) -> RuntimeMessage:
    """A self-monitoring signal still fires after the agent's response (one per episode). An
    ``entity`` that is a record id (thrash: the experiment) becomes a ref."""
    key = code if entity is None else f"{code}:{entity}"
    refs = []
    entity_name = _NAME_BY_PREFIX.get(entity.partition("-")[0]) if entity else None
    if entity and ID_TOKEN.fullmatch(entity) and entity_name in ENTITY_PREFIXES:
        refs.append({"uri": f"ar://{entity_name}/{entity}", "role": "subject"})
    body = (
        f"One of the agent's self-checks keeps firing: {detail}. It first fired at "
        f"{first_seen} and still fires {iterations} iterations (card runs) later, after the "
        "agent tried to respond. Please look at the research state and send a steer."
    )
    return RuntimeMessage(
        "help_request",
        _title("Warning sign not clearing", code.replace("_", " ")),
        body,
        refs,
        about=_about(signal=key),
        key=f"signal:{key}@{first_seen}",
        legacy_title=f"Signal {key} (since {first_seen}) persists",
    )


def agent_paused(reasons: str, paused_at: str, key: str | None = None) -> RuntimeMessage:
    """queue-keeper preflight failed and paused the agent: one per pause, or one per ``key``
    when given (a streak of unavailable ``whoami`` runs)."""
    body = (
        f"The agent is paused because the preflight check failed: {reasons}. No new cards "
        "start, and pending cards were unassigned. Running cards stop at their next identity "
        "check. Please fix the cause: the agent resumes by itself on the first run where the "
        "preflight check passes."
    )
    return RuntimeMessage(
        "help_request",
        "Agent paused: preflight failed",
        body,
        key=key or f"paused:{paused_at}",
        legacy_title=None if key else f"Agent paused at {paused_at}",
    )


def agent_resumed(count: int, champion: bool = False, provenance: bool = False) -> RuntimeMessage:
    """The agent resumed; ``champion``: the pause was a champion conflict (A9);
    ``provenance``: the pause was a commit the runtime did not make (``runtime.provenance``)."""
    if champion:
        why = "The league and the agent agree on the champion again"
    elif provenance:
        why = "Every commit on the workspace is now one the runtime made or a person accepted"
    else:
        why = (
            "The cause of the pause has cleared (the preflight check passes and today's spend "
            "is under the daily budget)"
        )
    body = (
        f"{why}, so the agent resumed. {count} pending card(s) were assigned again. Nothing "
        "needs to be done."
    )
    return RuntimeMessage("note", "Agent resumed", body)


_FILES_LISTED = 8
_PROVENANCE_TITLES = {
    "unknown_commits": "Agent paused: the workspace has commits the runtime did not make",
    "rewritten": "Agent paused: the workspace history was rewritten",
    "journal_lost": "Agent paused: the runtime's record of its commits is missing",
}


def _commit_line(commit: dict) -> str:
    files = [plain_ids(path) for path in commit["files"]]
    shown = ", ".join(files[:_FILES_LISTED]) or "no files"
    more = len(files) - _FILES_LISTED
    return (
        f"by {commit['author']} at {commit['at']}, changing {shown}"
        + (f" and {more} more" if more > 0 else "")
    )


def commit_provenance(violation: dict, day: str) -> RuntimeMessage:
    """queue-keeper found commits on canonical ``main`` that the runtime did not make, a
    rewritten history, or a lost journal (``runtime.provenance.Violation.to_dict()``), and
    paused the agent. One per set of unrecorded commits (per rewrite, per day for a lost
    journal)."""
    kind, commits = violation["kind"], violation["commits"]
    if kind == "unknown_commits":
        what = (
            f"{len(commits)} commit(s) on the main branch of the canonical workspace were not "
            "made by the runtime. Only the runtime writes there, so a card worker or another "
            "process probably worked around a guard."
        )
        key = "provenance:" + hashlib.sha256(
            " ".join(commit["sha"] for commit in commits).encode()
        ).hexdigest()[:16]
    elif kind == "rewritten":
        what = (
            "The history of the main branch of the canonical workspace was rewritten (a "
            "reset, rebase, amend or forced move): "
            f"{violation['dropped']} commit(s) the runtime had checked are no longer part of it"
            + (f", and {len(commits)} newer commit(s) were not made by the runtime." if commits
               else ".")
        )
        key = f"provenance-rewritten:{violation['base']}"
    else:
        what = (
            "The runtime's record of the commits it makes on the canonical workspace (in the "
            "workspace's .git/autoresearch/) is missing or no longer verifies, so it cannot "
            "tell its own commits from others."
        )
        key = f"provenance-journal:{day}"
    listed = f"\n\n{_bullets([_commit_line(commit) for commit in commits])}" if commits else ""
    body = (
        f"{what}{listed}\n\nThe agent is paused: no new cards start, pending cards were "
        "unassigned, and finished cards are not merged until a person looks. To inspect, run "
        '`"$AUTORESEARCH_CLI" runtime check-commits` in an operator shell: it lists each '
        "commit with its id and files (the ids are also in this message's details, `about`). "
        "Keep the commits, or undo them with git revert in the workspace (do not reset: the "
        "revert is one more commit to accept). When the history is as it should be, "
        'run `"$AUTORESEARCH_CLI" runtime accept-commits <commit>` with the commit '
        "`check-commits` names to accept (the agent cannot run it). The agent resumes by itself "
        "on the next run."
    )
    return RuntimeMessage(
        "help_request",
        _PROVENANCE_TITLES[kind],
        body,
        about=_about(
            commits=" ".join(commit["sha"] for commit in commits),
            accept_commit=violation["accept_sha"],
            head=violation["head"],
            last_checked=violation["base"],
        ),
        key=key,
    )


def commits_accepted(sha: str, accepted_at: str, author: str, subject: str) -> RuntimeMessage:
    """Someone ran ``runtime accept-commits`` (or wrote its journal entry): the runtime now
    trusts that commit and its history. One per accept, so an accept is never silent."""
    body = (
        f"At {accepted_at} the workspace history was accepted up to a commit by {author} "
        f"(“{_line(plain_ids(subject), 80)}”): the runtime now trusts it and everything before "
        "it, and the agent resumes if that was the only reason for its pause. The commit is in "
        "this message's details (`about`). If you did not run accept-commits, someone else "
        "did: check the workspace history and the journal."
    )
    return RuntimeMessage(
        "note",
        "Workspace commits accepted",
        body,
        about=_about(accepted_commit=sha),
        key=f"provenance-accepted:{sha}:{accepted_at}",
    )


def staged_not_published(
    message_id: str, staged_title: str | None, problems: list[str]
) -> RuntimeMessage:
    """A message a card staged that the publisher skipped (invalid envelope)."""
    body = (
        "A message the agent wrote was not published, because it does not fit the message "
        f"format:\n\n{_bullets(problems)}\n\nIt stays unpublished in comms/staged/. Nothing "
        "needs to be done unless this keeps happening."
    )
    return RuntimeMessage(
        "note",
        _title("Not published", staged_title or "an agent message with an invalid format"),
        body,
        about=_about(message=message_id),
        key=f"skipped-staged:{message_id}",
        legacy_title=f"Staged message {message_id} was not published",
    )


def push_failing(
    remote: str, failures: int, local_head: str, remote_head: str | None
) -> RuntimeMessage:
    body = (
        f"queue-keeper could not push the workspace to {remote} in {failures} runs in a row. "
        "Work that is not pushed is lost if this host is lost. Please check the remote URL, "
        "credentials and network."
    )
    return RuntimeMessage(
        "help_request",
        f"Push to {remote} failing",
        body,
        about=_about(local_head=local_head, remote_head=remote_head),
    )


def step_failing(step: str, runs: int, since: str, error: str, log_path: str) -> RuntimeMessage:
    """A queue-keeper step failed several runs in a row (one per failure episode)."""
    body = (
        f"The queue-keeper step {step} failed in each of the last {runs} runs, since {since}. "
        f"Whatever it does is not happening; the other steps go on. Last error: {error}. "
        f"Details are in {log_path}."
    )
    return RuntimeMessage(
        "help_request",
        f"queue-keeper step {step} failing",
        body,
        key=f"step-failing:{step}:{since}",
        legacy_title=f"queue-keeper step {step} failing since {since}",
    )


# --- promotion, rebuild, proposals ---


def promotion_stuck(
    promotion_id: str,
    recovery_requests: list[str],
    champion_path: str,
    submission_id: str | None,
    policy_version_id: str | None,
    candidate_ref: str,
    candidate_sha: str | None,
    refs: list,
) -> RuntimeMessage:
    """A promotion's recovery attempts are exhausted; an operator must act."""
    body = (
        f"A promotion submitted a new policy, but {_count(recovery_requests, 'recovery card')} "
        "ended without recording the new champion. No more recovery cards will be made, and no "
        "new promotion can start, until an operator records the champion file "
        f"{champion_path} from the candidate, or aborts the promotion. The candidate's ref and "
        "commit are in this message's details (`about`)."
    )
    about = _about(
        promotion=promotion_id,
        submission=submission_id,
        policy_version=policy_version_id,
        recovery_requests=", ".join(recovery_requests),
        candidate_ref=candidate_ref,
        candidate_commit=candidate_sha,
    )
    return RuntimeMessage(
        "help_request",
        "Promotion stuck: the new champion is not recorded",
        body,
        refs,
        about,
        key=f"promote-recover-help:{promotion_id}",
        legacy_title=f"(promote-recover-help:{promotion_id})",
    )


def champion_conflict(
    league_id: str, expected: str | None, shown: str | None, waited_out: bool
) -> RuntimeMessage:
    """The league's champion differs from what the loop expected (A9); queue-keeper paused
    the agent. One per (expected, shown) pair."""
    if waited_out:
        what = (
            "The agent submitted a new policy to the league, but the league still shows the "
            "policy it was meant to replace, well after the submission."
        )
    elif expected is None:
        what = "The league shows a champion for us that a person accepted as none."
    elif shown is None:
        what = "The league shows no policy competing for us, but the agent expected one."
    else:
        what = (
            "The league shows a different policy competing for us than the one the agent "
            "expected (for example a policy submitted by hand)."
        )
    body = (
        f"{what} The league decides the champion, so the agent now compares against what the "
        "league shows, but it is paused until a person looks: no new cards start. The "
        "expected and shown policies are in this message's details (`about`). To resolve it, "
        f"either make the {league_id} league show the expected policy again, or accept what "
        'it shows by running `"$AUTORESEARCH_CLI" promotion accept-champion` in an operator '
        "shell (the agent cannot run it). The agent resumes by itself on the first run where "
        "they agree."
    )
    return RuntimeMessage(
        "help_request",
        "Agent paused: the league shows an unexpected champion",
        body,
        about=_about(expected_policy=expected or "none", league_policy=shown or "none"),
        key=f"champion-conflict:{expected or 'none'}:{shown or 'none'}",
    )


def submission_rejected(
    league_id: str,
    promotion_id: str,
    policy: str,
    replaced: str | None,
    detail: str,
    bootstrap: bool = False,
) -> RuntimeMessage:
    """The league did not take a policy the agent submitted (A9): rejected, withdrawn, or its
    membership disqualified. Published once per promotion. A rejected first champion (the
    game's starter policy) needs the human; any other rejection does not."""
    if bootstrap:
        body = (
            f"The {league_id} league did not accept the game's starter policy, which the agent "
            "submitted as its first champion. There is still no champion, so the agent cannot "
            "evaluate experiments and will not submit the starter policy again by itself. To "
            "go on, either submit a different starter policy to the league by hand (the agent "
            'then uses it as the champion), or run `"$AUTORESEARCH_CLI" promotion '
            "accept-champion` in an operator shell to let the agent submit the starter policy "
            "again (for example after fixing why it was rejected). The league's status is in "
            "this message's details (`about`)."
        )
        kind, title = "help_request", "The league rejected the first champion"
    else:
        body = (
            f"The {league_id} league did not take a policy the agent submitted. The league "
            "decides, so that promotion is closed, the agent keeps the champion it had before "
            "(none, if there was none), and it goes on comparing experiments against what the "
            "league shows. Nothing needs to be done unless you expected the submission to be "
            "accepted; then check its league status (in this message's details, `about`)."
        )
        kind, title = "note", "The league did not accept a submitted policy"
    return RuntimeMessage(
        kind,
        title,
        body,
        about=_about(
            promotion=promotion_id,
            submitted_policy=policy,
            previous_champion=replaced or "none",
            league_status=detail,
        ),
        key=f"submission-rejected:{promotion_id}",
    )


def submission_pending(
    league_id: str, promotion_id: str, policy: str, detail: str, hours: int
) -> RuntimeMessage:
    """A submission the league has not decided on after ``hours`` (A9): one per promotion."""
    body = (
        f"A policy the agent submitted to the {league_id} league is still waiting for the "
        f"league's decision after {hours} hours (it may be held for an operator). The agent "
        "keeps comparing against the current champion meanwhile and cannot start another "
        "promotion until the league decides. Please check the submission's league status "
        "(in this message's details, `about`)."
    )
    return RuntimeMessage(
        "help_request",
        "A submitted policy is still waiting for the league",
        body,
        about=_about(promotion=promotion_id, submitted_policy=policy, league_status=detail),
        key=f"submission-pending:{promotion_id}",
    )


# --- the coach's IDE Controls (Softmax hosting) ---

_STOP_ASKING = {
    "xp": (
        "To stop being asked for each request, change Ask before in the IDE Controls: remove "
        "“spend” to let the agent spend up to your daily credits without asking, or replace "
        "it with “spend-over-N” to be asked only for a spend of more than N credits. Without "
        "saved rules, Softmax asks before every action."
    ),
    "submit": (
        "To stop being asked before each league submission, remove “submit” from Ask before in "
        "the IDE Controls. Without saved rules, Softmax asks before every action."
    ),
}


def _subject_about(act: str, subject: str, **ids) -> dict[str, str]:
    return _about(**{"experiment_batch" if act == "xp" else "promotion": subject}, **ids)


def permit_needed(
    act: str,
    subject: str,
    count: int,
    asks: list[str],
    league_id: str | None = None,
    over_cap: bool = False,
    refs: list | None = None,
    reminder: bool = False,
    key_asks: list[str] | None = None,
) -> RuntimeMessage:
    """Softmax holds ``count`` acts until the coach answers their permit asks (403
    ``permit_required``: the IDE Controls ask before them). ``act`` is ``xp`` (``subject``:
    ``<E>:b<batch>:a<attempt>``, every refused request of one batch) or ``submit``
    (``subject``: the promotion id). One per subject and set of ``asks``, however often it is
    refused again: new asks (a yes the price outgrew) are a new message. ``key_asks`` (default
    ``asks``) is the set the key is made from, so it can stay the same while ``asks`` shrinks
    (asks drop out as their requests are created). ``reminder``: the one reminder about the
    same asks, sent once a day after the first."""
    if act == "xp":
        what = (
            f"The agent asked Softmax for {count} hosted XP request(s) to evaluate one of its "
            "experiments (each plays one seed, for the candidate or for the champion). Your IDE "
            f"Controls ask before each spend of credits, so Softmax filed {count} permit ask(s) "
            "and holds the requests. Approve them in the IDE Controls. The agent reads your "
            "answers every few minutes and goes on once all are approved; nothing is created "
            "twice. If you answer no to any of them, the agent stops this experiment and asks "
            "nothing more for it."
        )
        if over_cap:
            what += (
                " Some of them cost more than the credits left today under your daily limit, "
                "so the agent waits for the next UTC day before it tries them again, whatever "
                "you answer before then."
            )
    else:
        what = (
            f"The agent is ready to submit a policy to the {league_id} league, and your IDE "
            "Controls ask before league submissions, so Softmax filed a permit ask and holds "
            "the submission. Approve it in the IDE Controls. The runtime reads your answer "
            "every minute and submits once you approve, never twice. If you answer no, the "
            "promotion is dropped and nothing more is asked for it."
        )
    body = f"{what} {_STOP_ASKING[act]} The asks are in this message's details (`about`)."
    title = "Waiting for your approval in the IDE Controls"
    if reminder:
        body = f"A reminder: these asks have waited a day for your answer. {body}"
        title = "Still waiting for your approval in the IDE Controls"
    ask_set = hashlib.sha256("|".join(sorted(key_asks or asks)).encode()).hexdigest()[:12]
    return RuntimeMessage(
        "help_request",
        title,
        body,
        refs or [],
        about=_subject_about(act, subject, asks=", ".join(asks), league=league_id),
        key=f"permit-needed:{subject}:{ask_set}" + (":reminder" if reminder else ""),
    )


def league_not_chosen(
    act: str, league_id: str, reason: str, retry_minutes: int, refs: list | None = None
) -> RuntimeMessage:
    """Softmax refused a league-scoped act (``act``: ``xp`` or ``submit``) because the coach
    chose no league for the agent, or another one. One per league and cause, whatever acts
    and experiments it holds up."""
    chosen_none = reason.startswith("Your coach has not chosen")
    why = (
        "no league is chosen for the agent yet"
        if chosen_none
        else "the IDE Controls choose a different league for the agent"
    )
    asked = "to run hosted games" if act == "xp" else "to submit a policy"
    body = (
        f"The agent asked Softmax {asked} in the {league_id} league, and Softmax refused because "
        f"{why}: the agent plays only the one league its coach chooses. To go on, choose the "
        f"{league_id} league for the agent in the IDE Controls. (The agent's league is set in "
        "its game's configuration; if it should play another league, ask the operator to change "
        "that instead.) The agent retries by itself: first "
        f"{retry_minutes} minutes after the refusal, then twice as long each time (at most 8 "
        "hours), so it can take that long to go on after you choose. Softmax's words are in this "
        "message's details (`about`)."
    )
    return RuntimeMessage(
        "help_request",
        "Choose the agent's league in the IDE Controls",
        body,
        refs or [],
        about=_about(league=league_id, server_reason=reason),
        key=f"league-not-chosen:{league_id}:{'none' if chosen_none else 'elsewhere'}",
    )


def coach_declined(
    act: str, subject: str, done: int = 0, total: int = 0, refs: list | None = None
) -> RuntimeMessage:
    """The coach answered no to a permit ask: the experiment is stopped (``xp``), the
    promotion dropped (``submit``), or the first champion's submission dropped
    (``bootstrap``: the starter policy; research waits for a champion). One per subject;
    nothing is asked again for it."""
    if act == "xp":
        body = (
            "You answered no to a permit ask for one of the agent's experiments, so the agent "
            f"stopped that experiment with {done} of its {total} XP requests run. It asks nothing "
            "more for it, and its results so far are kept but not reviewed. Its other asks that "
            "are still open in the IDE Controls can be left unanswered. Nothing needs to be done."
        )
        title = "Experiment stopped: you declined its XP requests"
    elif act == "bootstrap":
        body = (
            "You answered no to the agent's submission of the game's starter policy as its first "
            "champion. Nothing was submitted, and there is still no champion, so the agent's "
            "research is paused: it cannot evaluate experiments without one, and it will not ask "
            "again by itself. To allow a submission, have the operator run "
            '`"$AUTORESEARCH_CLI" promotion accept-champion` in an operator shell; the agent then '
            "submits the starter policy again and you get a new ask to approve (or none, if you "
            "removed “submit” from Ask before in the IDE Controls)."
        )
        title = "No first champion: you declined the starter policy's submission"
    else:
        body = (
            "You answered no to the agent's league submission, so that promotion is dropped: "
            "nothing was submitted, the champion stays as it is, and the agent may promote "
            "another policy later. Nothing needs to be done."
        )
        title = "Promotion dropped: you declined its submission"
    about = _subject_about(act, subject)
    return RuntimeMessage("note", title, body, refs or [], about, key=f"coach-declined:{subject}")


def relaunch_stuck(
    experiment: str, marker: str, generations: int, refs: list | None = None
) -> RuntimeMessage:
    """The relaunch of an experiment's waiting XP batch failed ``generations`` times without a
    new refusal (each card ended without creating the requests), so the runtime stops
    relaunching it. One per wait: ``marker`` is the wait's relaunch marker
    (``markers.relaunch_marker``), which every new refusal or denial changes."""
    body = (
        f"The agent tried {generations} times to send an experiment's waiting XP requests again "
        "after they were cleared to run, and each try ended without creating them and without "
        "a new refusal from Softmax (for example a platform error). It has stopped retrying so "
        "it does not keep spending model calls. The requests and their approvals are kept. "
        "Ask the operator to look at the experiment's last launch cards; a new launch card for "
        "the same batch sends the same requests again."
    )
    return RuntimeMessage(
        "help_request",
        "XP requests could not be sent: relaunches keep failing",
        body,
        refs or [],
        about=_about(experiment=experiment, relaunch=marker),
        key=f"relaunch-stuck:{marker}",
    )


def request_lost_in_rebuild(request_id: str, mode: str | None) -> RuntimeMessage:
    """An admitted request a board rebuild cannot re-admit (its spec was never committed)."""
    card = f"{mode} card" if mode else "card"
    body = (
        f"The work board was lost and rebuilt. One requested {card}, admitted but not "
        "finished, could not be made again: its request was never committed, so its details "
        "are gone. It is now closed as rejected. If the work still matters, request it again."
    )
    return RuntimeMessage(
        "help_request",
        "Requested card lost in a board rebuild",
        body,
        about=_about(request=request_id),
        key=f"rebuild-lost:{request_id}",
        legacy_title=f"Request {request_id} lost in a board rebuild",
    )


def skill_proposal(name: str, tree_hash: str, directory: str, rev: str) -> RuntimeMessage:
    """A new or changed skill the agent staged; one per content version."""
    body = (
        f"The agent wrote or changed the skill {name}. It is in {directory}/{name}/ and is not "
        "adopted yet. Reply adopt, reject, or revise."
    )
    refs = [{"uri": f"ar://skill-proposal/{name}", "rev": rev}]
    return RuntimeMessage(
        "suggestion",
        _title("Skill proposal", name),
        body,
        refs,
        key=f"{skill_proposal_key_prefix(name)}{tree_hash}",
        legacy_title=f"{skill_proposal_legacy_prefix(name)}{tree_hash})",
    )


def skill_proposal_key_prefix(name: str) -> str:
    return f"skill-proposal:{name}:"


def skill_proposal_legacy_prefix(name: str) -> str:
    """The start of a skill proposal's title before runtime_key existed: ``Skill proposal:
    <name> (<tree hash>)``."""
    return f"Skill proposal: {name} ("


def proposal_still_open(message: dict, rev: str) -> RuntimeMessage:
    """A re-raise of an open proposal that has had no reply for a day. A skill proposal
    published before runtime_key existed has its content hash in the title; it is dropped."""
    body = f"This still has no reply (first sent {message['created']}). {message['body']}"
    refs = [ref | {"rev": rev} for ref in message["refs"]]
    title = plain_ids(_LEGACY_HASH.sub("", message["title"]))
    return RuntimeMessage("suggestion", _title("Still open", title), body, refs)

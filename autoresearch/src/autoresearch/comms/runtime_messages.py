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
  (``publisher.outbox_has_key``).

Each builder here returns a ``RuntimeMessage``; ``publisher.publish_message`` publishes it.
``tests/contract/comms/test_runtime_messages.py`` publishes every builder and checks that no
id or commit reaches the text, and that runtime code publishes nothing else.
"""

from dataclasses import dataclass, field

TITLE_LIMIT = 120
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
# What follows from the outcome, for the reader.
_NEXT = {
    "partial": "The rest of the work continues in a follow-up card.",
    "dropped": "No more work will be done on it. Send a new task if it still matters.",
    "failed": "No more work will be done on it. Send a new task if it still matters.",
    "blocked": "The agent needs help to go on; its help request says what it needs.",
}
# ``about.outcome`` of a note that answers an inbound it did not act on, so a reader such as
# the IDE bridge can tell a rejection from a card outcome without parsing the title.
REJECTED = "rejected"
_SENT_LISTED = 3
_UNRESOLVED_REF = "unresolved ref "
_REBUILD_LOST = "board rebuild could not re-admit "


@dataclass(frozen=True)
class RuntimeMessage:
    kind: str
    title: str
    body: str
    refs: list = field(default_factory=list)
    about: dict[str, str] = field(default_factory=dict)
    key: str | None = None  # the runtime_key: published once

    def fields(self) -> dict:
        """The additive envelope fields of this message."""
        found: dict = {}
        if self.about:
            found["about"] = dict(self.about)
        if self.key is not None:
            found["runtime_key"] = self.key
        return found


def _line(text: str, limit: int = TITLE_LIMIT) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _title(lead: str, subject: str | None) -> str:
    return _line(f"{lead}: {subject}" if subject and subject.strip() else lead)


def _about(**ids) -> dict[str, str]:
    return {name: str(value) for name, value in ids.items() if value not in (None, "", [])}


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _quoted_list(titles: list[str]) -> str:
    shown = [f"“{_line(title, 80)}”" for title in titles[:_SENT_LISTED]]
    more = len(titles) - len(shown)
    return "; ".join(shown) + (f"; and {more} more" if more else "")


# --- inbound task: acknowledgement and rejection ---


def task_acknowledgement(
    task_title: str | None,
    outcome: str,
    summary: str | None,
    sent_titles: list[str],
    backlog: bool,
    has_journal: bool,
    refs: list,
    about: dict[str, str],
) -> RuntimeMessage:
    """The note that closes an inbound ``task`` once its card is published (C11). ``summary``
    is the card's own completion summary, if it left one; ``sent_titles`` the titles of the
    messages the card published."""
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
    if sent_titles:
        parts.append(f"The agent also sent: {_quoted_list(sent_titles)}.")
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
        "help_request", f"Health check: {kind} problem", body, key=f"health:{kind}:{day}"
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
    )


# --- queue-keeper ---


def signal_persists(
    code: str, entity: str | None, detail: str, first_seen: str, iterations: int
) -> RuntimeMessage:
    """A self-monitoring signal still fires after the agent's response (one per episode)."""
    key = code if entity is None else f"{code}:{entity}"
    body = (
        f"One of the agent's self-checks keeps firing: {detail}. It first fired at "
        f"{first_seen} and still fires {iterations} iterations (card runs) later, after the "
        "agent tried to respond. Please look at the research state and send a steer."
    )
    return RuntimeMessage(
        "help_request",
        _title("Warning sign not clearing", code.replace("_", " ")),
        body,
        about=_about(signal=key),
        key=f"signal:{key}@{first_seen}",
    )


def agent_paused(reasons: str, paused_at: str) -> RuntimeMessage:
    """queue-keeper preflight failed and paused the agent (one per pause)."""
    body = (
        f"The agent is paused because the preflight check failed: {reasons}. No new cards "
        "start, and pending cards were unassigned. Running cards stop at their next identity "
        "check. Please fix the cause: the agent resumes by itself on the first run where the "
        "preflight check passes."
    )
    return RuntimeMessage(
        "help_request", "Agent paused: preflight failed", body, key=f"paused:{paused_at}"
    )


def agent_resumed(count: int) -> RuntimeMessage:
    body = (
        f"The preflight check passes again, so the agent resumed. {count} pending card(s) "
        "were assigned again. Nothing needs to be done."
    )
    return RuntimeMessage("note", "Agent resumed", body)


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
        f"A promotion submitted a new policy, but its {len(recovery_requests)} recovery cards "
        "all ended without recording the new champion. No more recovery cards will be made, "
        "and no new promotion can start, until an operator records the champion file "
        f"{champion_path} from the candidate, or aborts the promotion. This message's `about` "
        "field names the candidate's ref and commit."
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
    )


def skill_proposal_key_prefix(name: str) -> str:
    return f"skill-proposal:{name}:"


def proposal_still_open(message: dict, rev: str) -> RuntimeMessage:
    """A re-raise of an open proposal that has had no reply for a day."""
    body = f"This still has no reply (first sent {message['created']}). {message['body']}"
    refs = [ref | {"rev": rev} for ref in message["refs"]]
    return RuntimeMessage("suggestion", _title("Still open", message["title"]), body, refs)
